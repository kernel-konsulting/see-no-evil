# METADATA
# title: see-no-evil decision policy
# description: |
#   Mirrors the decision order in services/api/src/seenoevil_api/policy.py.
#   Decision rules emit {"decision": "allow"|"block", "reason": "..."}.
#   Order: schedule, quota, deny_domain, deny_keyword, youtube channel rules,
#          allow list, classifier thresholds, default-allow.
package seenoevil.policy

import rego.v1

# ---------------------------------------------------------------------------
# Public decision
# ---------------------------------------------------------------------------

default decision := {"decision": "allow", "reason": "default"}

decision := r if {
	some r in candidate_blocks
}

# ---------------------------------------------------------------------------
# Block candidates (first match wins, evaluated in priority order via array)
# ---------------------------------------------------------------------------

candidate_blocks := blocks if {
	blocks := [b |
		some rule in [
			schedule_block,
			quota_block,
			deny_domain_block,
			deny_keyword_block,
			youtube_deny_block,
			youtube_allowlist_block,
			allowlist_block,
			classifier_block,
		]
		b := rule
		b != null
	]
	count(blocks) > 0
}

# ---------------------------------------------------------------------------
# 1. Schedule
# ---------------------------------------------------------------------------

schedule_block := {"decision": "block", "reason": "schedule"} if {
	count(input.profile.schedule) > 0
	not in_any_schedule_window
}

schedule_block := null if {
	in_any_schedule_window
}

schedule_block := null if {
	count(input.profile.schedule) == 0
}

in_any_schedule_window if {
	some key, spec in input.profile.schedule
	day_matches(key, input.now_dow)
	in_window(spec, input.now_time_minutes)
}

day_matches(key, dow) if {
	weekdays := {
		"monday": {0}, "tuesday": {1}, "wednesday": {2}, "thursday": {3},
		"friday": {4}, "saturday": {5}, "sunday": {6},
		"weekdays": {0, 1, 2, 3, 4}, "monday_friday": {0, 1, 2, 3, 4},
		"weekend": {5, 6}, "saturday_sunday": {5, 6},
		"everyday": {0, 1, 2, 3, 4, 5, 6}, "all": {0, 1, 2, 3, 4, 5, 6},
	}
	dow in weekdays[lower(key)]
}

in_window(spec, t) if {
	parts := split(spec, "-")
	count(parts) == 2
	start_min := minutes_of(parts[0])
	end_min := minutes_of(parts[1])
	start_min <= end_min
	t >= start_min
	t <= end_min
}

in_window(spec, t) if {
	parts := split(spec, "-")
	count(parts) == 2
	start_min := minutes_of(parts[0])
	end_min := minutes_of(parts[1])
	start_min > end_min # window wraps midnight
	t >= start_min
} else if {
	parts := split(spec, "-")
	count(parts) == 2
	start_min := minutes_of(parts[0])
	end_min := minutes_of(parts[1])
	start_min > end_min # window wraps midnight (other half)
	t <= end_min
}

minutes_of(hhmm) := mins if {
	p := split(trim_space(hhmm), ":")
	count(p) == 2
	mins := (to_number(p[0]) * 60) + to_number(p[1])
}

# ---------------------------------------------------------------------------
# 2. Quota
# ---------------------------------------------------------------------------

quota_block := {"decision": "block", "reason": "quota"} if {
	input.profile.quota_minutes_per_day > 0
	input.minutes_used_today >= input.profile.quota_minutes_per_day
}

quota_block := null if {
	input.profile.quota_minutes_per_day == 0
}

quota_block := null if {
	input.minutes_used_today < input.profile.quota_minutes_per_day
}

# ---------------------------------------------------------------------------
# 3. Deny domains
# ---------------------------------------------------------------------------

deny_domain_block := {"decision": "block", "reason": "deny_domain"} if {
	some pattern in input.profile.deny_domains
	host_matches(pattern, input.host)
}

deny_domain_block := null if {
	not deny_domain_match
}

deny_domain_match if {
	some pattern in input.profile.deny_domains
	host_matches(pattern, input.host)
}

# ---------------------------------------------------------------------------
# 4. URL keyword
# ---------------------------------------------------------------------------

deny_keyword_block := {"decision": "block", "reason": "deny_keyword"} if {
	some kw in input.profile.deny_url_keywords
	contains(lower(input.path_query), lower(trim_space(kw)))
}

deny_keyword_block := null if {
	not deny_keyword_match
}

deny_keyword_match if {
	some kw in input.profile.deny_url_keywords
	contains(lower(input.path_query), lower(trim_space(kw)))
}

# ---------------------------------------------------------------------------
# 5. YouTube channel rules
# ---------------------------------------------------------------------------

is_youtube if {
	h := lower(input.host)
	h == "youtube.com"
}

is_youtube if {
	h := lower(input.host)
	endswith(h, ".youtube.com")
}

is_youtube if {
	h := lower(input.host)
	h == "youtu.be"
}

youtube_deny_block := {"decision": "block", "reason": "deny_youtube_channel"} if {
	is_youtube
	input.youtube_channel != null
	some pattern in input.profile.deny_youtube_channels
	channel_matches(pattern, input.youtube_channel)
}

youtube_deny_block := null if {
	not youtube_deny_match
}

youtube_deny_match if {
	is_youtube
	input.youtube_channel != null
	some pattern in input.profile.deny_youtube_channels
	channel_matches(pattern, input.youtube_channel)
}

youtube_allowlist_block := {"decision": "block", "reason": "youtube_channel_not_allowed"} if {
	is_youtube
	count(input.profile.allow_youtube_channels) > 0
	not youtube_channel_allowed
}

youtube_allowlist_block := null if {
	not is_youtube
}

youtube_allowlist_block := null if {
	is_youtube
	count(input.profile.allow_youtube_channels) == 0
}

youtube_allowlist_block := null if {
	youtube_channel_allowed
}

youtube_channel_allowed if {
	input.youtube_channel != null
	some pattern in input.profile.allow_youtube_channels
	channel_matches(pattern, input.youtube_channel)
}

# ---------------------------------------------------------------------------
# 6. Allow list
# ---------------------------------------------------------------------------

allowlist_block := {"decision": "block", "reason": "not_in_allowlist"} if {
	count(input.profile.allow_domains) > 0
	not allow_domain_match
}

allowlist_block := null if {
	count(input.profile.allow_domains) == 0
}

allowlist_block := null if {
	allow_domain_match
}

allow_domain_match if {
	some pattern in input.profile.allow_domains
	host_matches(pattern, input.host)
}

# ---------------------------------------------------------------------------
# 7. Classifier thresholds
# ---------------------------------------------------------------------------

classifier_block := {"decision": "block", "reason": sprintf("classifier:%s", [cls])} if {
	some cls, score in input.classifier_scores
	threshold := input.profile.image_thresholds[cls]
	score >= threshold
}

classifier_block := null if {
	not classifier_match
}

classifier_match if {
	some cls, score in input.classifier_scores
	threshold := input.profile.image_thresholds[cls]
	score >= threshold
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

host_matches(pattern, host) if {
	p := lower(trim_left(trim_space(pattern), "."))
	h := lower(host)
	p != ""
	h != ""
	h == p
}

host_matches(pattern, host) if {
	p := lower(trim_left(trim_space(pattern), "."))
	h := lower(host)
	p != ""
	h != ""
	endswith(h, concat("", [".", p]))
}

host_matches(pattern, host) if {
	p_raw := lower(trim_space(pattern))
	startswith(p_raw, "*.")
	suffix := substring(p_raw, 2, -1)
	h := lower(host)
	h == suffix
}

host_matches(pattern, host) if {
	p_raw := lower(trim_space(pattern))
	startswith(p_raw, "*.")
	suffix := substring(p_raw, 2, -1)
	h := lower(host)
	endswith(h, concat("", [".", suffix]))
}

channel_matches(pattern, channel) if {
	p := lower(trim_left(trim_space(pattern), "@"))
	c := lower(trim_left(trim_space(channel), "@"))
	p != ""
	c != ""
	p == c
}

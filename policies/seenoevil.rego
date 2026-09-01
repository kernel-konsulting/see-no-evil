# METADATA
# title: see-no-evil decision policy
# description: |
#   Mirrors the decision order in services/api/src/seenoevil_api/policy.py.
#   Decision rules emit {"decision": "allow"|"block", "reason": "..."}.
#   Order: schedule, quota, global deny/allow, global keyword, deny_domain,
#          deny_keyword, youtube channel rules, allow list, classifier thresholds, default-allow.
package seenoevil.policy

import rego.v1

# ---------------------------------------------------------------------------
# Public decision — first match wins (block or allow)
# ---------------------------------------------------------------------------

default decision := {"decision": "allow", "reason": "default"}

decision := result if {
	some i, r in ordered_results
	r != null
	result := r
	not has_earlier_non_null(ordered_results, i)
}

has_earlier_non_null(arr, i) if {
	some j, v in arr
	j < i
	v != null
}

ordered_results := [r |
	some rule in [
		schedule_block,
		quota_block,
		global_deny_domain_block,
		global_allow_domain_result,
		global_not_in_allowlist_block,
		global_deny_keyword_block,
		deny_domain_block,
		deny_keyword_block,
		youtube_deny_block,
		youtube_allowlist_block,
		allow_domain_result,
		not_in_allowlist_block,
		classifier_block,
	]
	r := rule
]

# ---------------------------------------------------------------------------
# Helpers: input defaults
# ---------------------------------------------------------------------------

global_rules := g if {
	g := object.get(input, "global_rules", {})
}

global_allow_domains := a if {
	a := object.get(global_rules, "allow_domains", [])
}

global_deny_domains := d if {
	d := object.get(global_rules, "deny_domains", [])
}

global_deny_keywords := k if {
	k := object.get(global_rules, "deny_url_keywords", [])
}

global_enforce_allowlist := e if {
	e := object.get(global_rules, "enforce_allowlist", false)
}

global_image_thresholds := t if {
	t := object.get(global_rules, "image_thresholds", {})
}

config_thresholds := t if {
	t := object.get(input, "config_thresholds", {})
}

apply_domain_rules := v if {
	v := object.get(global_rules, "apply_domain_rules", true)
}

apply_url_rules := v if {
	v := object.get(global_rules, "apply_url_rules", true)
}

enforce_allowlist := v if {
	v := object.get(input.profile, "enforce_allowlist", false)
}

profile_allow_domains := d if {
	d := object.get(input.profile, "allow_domains", [])
}

profile_deny_domains := d if {
	d := object.get(input.profile, "deny_domains", [])
}

profile_deny_keywords := k if {
	k := object.get(input.profile, "deny_url_keywords", [])
}

profile_image_thresholds := t if {
	t := object.get(input.profile, "image_thresholds", {})
}

# panic_relax short-circuit — kept for completeness though router handles it outside OPA
panic_result := {"decision": "allow", "reason": "panic_relax"} if {
	input.panic_relax == true
}

# ---------------------------------------------------------------------------
# 1. Schedule
# ---------------------------------------------------------------------------

schedule_block := {"decision": "block", "reason": "schedule"} if {
	count(object.get(input.profile, "schedule", {})) > 0
	not in_any_schedule_window
}

schedule_block := null if {
	in_any_schedule_window
}

schedule_block := null if {
	count(object.get(input.profile, "schedule", {})) == 0
}

in_any_schedule_window if {
	some key, spec in object.get(input.profile, "schedule", {})
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
	object.get(input.profile, "quota_minutes_per_day", 0) > 0
	object.get(input, "minutes_used_today", 0) >= object.get(input.profile, "quota_minutes_per_day", 0)
}

quota_block := null if {
	object.get(input.profile, "quota_minutes_per_day", 0) == 0
}

quota_block := null if {
	object.get(input, "minutes_used_today", 0) < object.get(input.profile, "quota_minutes_per_day", 0)
}

# ---------------------------------------------------------------------------
# 3. Global rules — domain allow/deny, allowlist enforcement, keyword
# ---------------------------------------------------------------------------

global_deny_domain_block := {"decision": "block", "reason": sprintf("global_deny_domain:%s", [m])} if {
	apply_domain_rules
	input.host != ""
	m := matched_pattern(global_deny_domains, input.host)
}

global_deny_domain_block := null if {
	not apply_domain_rules
}

global_deny_domain_block := null if {
	apply_domain_rules
	not host_in_any(global_deny_domains, input.host)
}

global_allow_domain_result := {"decision": "allow", "reason": sprintf("global_allow_domain:%s", [m])} if {
	apply_domain_rules
	input.host != ""
	m := matched_pattern(global_allow_domains, input.host)
}

global_allow_domain_result := null if {
	not apply_domain_rules
}

global_allow_domain_result := null if {
	apply_domain_rules
	not host_in_any(global_allow_domains, input.host)
}

global_not_in_allowlist_block := {"decision": "block", "reason": sprintf("global_not_in_allowlist:%s", [h])} if {
	apply_domain_rules
	global_enforce_allowlist
	count(global_allow_domains) > 0
	h := lower(input.host)
	not host_in_any(global_allow_domains, input.host)
}

global_not_in_allowlist_block := null if {
	not apply_domain_rules
}

global_not_in_allowlist_block := null if {
	not global_enforce_allowlist
}

global_not_in_allowlist_block := null if {
	count(global_allow_domains) == 0
}

global_not_in_allowlist_block := null if {
	host_in_any(global_allow_domains, input.host)
}

global_deny_keyword_block := {"decision": "block", "reason": sprintf("global_deny_keyword:%s", [m])} if {
	apply_url_rules
	m := matched_keyword(global_deny_keywords, lower(object.get(input, "url", "")))
}

global_deny_keyword_block := {"decision": "block", "reason": sprintf("global_deny_keyword:%s", [m])} if {
	apply_url_rules
	m := matched_keyword(global_deny_keywords, lower(object.get(input, "path_query", "")))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "url", "")))
}

# also check url_text when provided (Python joins url+path+query decoded)
global_deny_keyword_block := {"decision": "block", "reason": sprintf("global_deny_keyword:%s", [m])} if {
	apply_url_rules
	object.get(input, "url_text", "") != ""
	m := matched_keyword(global_deny_keywords, lower(input.url_text))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "url", "")))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "path_query", "")))
}

global_deny_keyword_block := null if {
	not apply_url_rules
}

global_deny_keyword_block := null if {
	not keyword_exists(global_deny_keywords, lower(object.get(input, "url", "")))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "path_query", "")))
	object.get(input, "url_text", "") == ""
}

global_deny_keyword_block := null if {
	not keyword_exists(global_deny_keywords, lower(object.get(input, "url", "")))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "path_query", "")))
	not keyword_exists(global_deny_keywords, lower(object.get(input, "url_text", "")))
}

# ---------------------------------------------------------------------------
# 4. Deny domains (profile)
# ---------------------------------------------------------------------------

deny_domain_block := {"decision": "block", "reason": sprintf("deny_domain:%s", [m])} if {
	apply_domain_rules
	input.host != ""
	m := matched_pattern(profile_deny_domains, input.host)
}

deny_domain_block := null if {
	not apply_domain_rules
}

deny_domain_block := null if {
	apply_domain_rules
	not host_in_any(profile_deny_domains, input.host)
}

# ---------------------------------------------------------------------------
# 5. URL keyword (profile)
# ---------------------------------------------------------------------------

deny_keyword_block := {"decision": "block", "reason": sprintf("deny_keyword:%s", [m])} if {
	apply_url_rules
	m := matched_keyword(profile_deny_keywords, lower(object.get(input, "url", "")))
}

deny_keyword_block := {"decision": "block", "reason": sprintf("deny_keyword:%s", [m])} if {
	apply_url_rules
	m := matched_keyword(profile_deny_keywords, lower(object.get(input, "path_query", "")))
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "url", "")))
}

deny_keyword_block := {"decision": "block", "reason": sprintf("deny_keyword:%s", [m])} if {
	apply_url_rules
	object.get(input, "url_text", "") != ""
	m := matched_keyword(profile_deny_keywords, lower(input.url_text))
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "url", "")))
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "path_query", "")))
}

deny_keyword_block := null if {
	not apply_url_rules
}

deny_keyword_block := null if {
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "url", "")))
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "path_query", "")))
	not keyword_exists(profile_deny_keywords, lower(object.get(input, "url_text", "")))
}

# ---------------------------------------------------------------------------
# 6. YouTube channel rules
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
	object.get(input, "youtube_channel", null) != null
	some pattern in object.get(input.profile, "deny_youtube_channels", [])
	channel_matches(pattern, input.youtube_channel)
}

youtube_deny_block := null if {
	not youtube_deny_match
}

youtube_deny_match if {
	is_youtube
	object.get(input, "youtube_channel", null) != null
	some pattern in object.get(input.profile, "deny_youtube_channels", [])
	channel_matches(pattern, input.youtube_channel)
}

youtube_allowlist_block := {"decision": "block", "reason": "youtube_channel_not_allowed"} if {
	is_youtube
	count(object.get(input.profile, "allow_youtube_channels", [])) > 0
	not youtube_channel_allowed
}

youtube_allowlist_block := null if {
	not is_youtube
}

youtube_allowlist_block := null if {
	is_youtube
	count(object.get(input.profile, "allow_youtube_channels", [])) == 0
}

youtube_allowlist_block := null if {
	youtube_channel_allowed
}

youtube_channel_allowed if {
	object.get(input, "youtube_channel", null) != null
	some pattern in object.get(input.profile, "allow_youtube_channels", [])
	channel_matches(pattern, input.youtube_channel)
}

# ---------------------------------------------------------------------------
# 7. Allow list (profile)
# ---------------------------------------------------------------------------

allow_domain_result := {"decision": "allow", "reason": sprintf("allow_domain:%s", [m])} if {
	apply_domain_rules
	input.host != ""
	m := matched_pattern(profile_allow_domains, input.host)
}

allow_domain_result := null if {
	not apply_domain_rules
}

allow_domain_result := null if {
	apply_domain_rules
	not host_in_any(profile_allow_domains, input.host)
}

not_in_allowlist_block := {"decision": "block", "reason": sprintf("not_in_allowlist:%s", [h])} if {
	apply_domain_rules
	enforce_allowlist
	count(profile_allow_domains) > 0
	not host_in_any(profile_allow_domains, input.host)
	h := lower(input.host)
	h != ""
}

not_in_allowlist_block := {"decision": "block", "reason": "not_in_allowlist:unknown"} if {
	apply_domain_rules
	enforce_allowlist
	count(profile_allow_domains) > 0
	not host_in_any(profile_allow_domains, input.host)
	lower(object.get(input, "host", "")) == ""
}

not_in_allowlist_block := null if {
	not apply_domain_rules
}

not_in_allowlist_block := null if {
	not enforce_allowlist
}

not_in_allowlist_block := null if {
	count(profile_allow_domains) == 0
}

not_in_allowlist_block := null if {
	host_in_any(profile_allow_domains, input.host)
}

# ---------------------------------------------------------------------------
# 8. Classifier thresholds
# ---------------------------------------------------------------------------

non_blocking_labels := {"neutral", "drawing", "drawings", "safe"}

classifier_block := {"decision": "block", "reason": sprintf("classifier:%s", [label])} if {
	some raw_cls, score in object.get(input, "classifier_scores", {})
	parts := split(raw_cls, ":")
	label := lower(parts[count(parts) - 1])
	not label in non_blocking_labels
	threshold := threshold_for_label(label)
	score >= threshold
}

classifier_block := null if {
	not classifier_match
}

classifier_match if {
	some raw_cls, score in object.get(input, "classifier_scores", {})
	parts := split(raw_cls, ":")
	label := lower(parts[count(parts) - 1])
	not label in non_blocking_labels
	threshold := threshold_for_label(label)
	score >= threshold
}

threshold_for_label(label) := t if {
	t := profile_image_thresholds[label]
} else := t if {
	t := global_image_thresholds[label]
} else := t if {
	t := config_thresholds[label]
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

matched_pattern(patterns, host) := m if {
	some p in patterns
	host_matches(p, host)
	m := lower(trim_left(trim_space(p), "."))
}

# host_in_any checks if host matches any pattern in list
host_in_any(patterns, host) if {
	some p in patterns
	host_matches(p, host)
}

keyword_exists(keywords, text) if {
	some kw in keywords
	trimmed := lower(trim_space(kw))
	trimmed != ""
	contains(text, trimmed)
}

matched_keyword(keywords, text) := m if {
	some kw in keywords
	trimmed := lower(trim_space(kw))
	trimmed != ""
	contains(text, trimmed)
	m := trimmed
}

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

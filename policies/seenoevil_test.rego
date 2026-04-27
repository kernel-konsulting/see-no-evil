package seenoevil.policy_test

import rego.v1

import data.seenoevil.policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

base_profile := {
    "image_thresholds": {"porn": 0.5},
    "schedule": {},
    "quota_minutes_per_day": 0,
    "allow_domains": [],
    "deny_domains": [],
    "deny_url_keywords": [],
    "allow_youtube_channels": [],
    "deny_youtube_channels": [],
}

base_input := {
    "url": "https://example.com/",
    "host": "example.com",
    "path_query": "/",
    "youtube_channel": null,
    "classifier_scores": {},
    "now_dow": 2,
    "now_time_minutes": 720,
    "minutes_used_today": 0,
    "profile": base_profile,
}

# ---------------------------------------------------------------------------
# Default-allow
# ---------------------------------------------------------------------------

test_default_allow if {
    out := policy.decision with input as base_input
    out == {"decision": "allow", "reason": "default"}
}

# ---------------------------------------------------------------------------
# Deny domain
# ---------------------------------------------------------------------------

test_deny_domain_blocks if {
    inp := object.union(base_input, {
        "host": "www.tiktok.com",
        "profile": object.union(base_profile, {"deny_domains": ["tiktok.com"]}),
    })
    out := policy.decision with input as inp
    out.decision == "block"
    out.reason == "deny_domain"
}

# ---------------------------------------------------------------------------
# URL keyword
# ---------------------------------------------------------------------------

test_deny_keyword_blocks if {
    inp := object.union(base_input, {
        "path_query": "/category/Nsfw/page",
        "profile": object.union(base_profile, {"deny_url_keywords": ["nsfw"]}),
    })
    out := policy.decision with input as inp
    out.decision == "block"
    out.reason == "deny_keyword"
}

# ---------------------------------------------------------------------------
# YouTube channel
# ---------------------------------------------------------------------------

test_youtube_deny_channel if {
    inp := object.union(base_input, {
        "host": "www.youtube.com",
        "youtube_channel": "@badchannel",
        "profile": object.union(base_profile, {"deny_youtube_channels": ["@badchannel"]}),
    })
    out := policy.decision with input as inp
    out.decision == "block"
    out.reason == "deny_youtube_channel"
}

test_youtube_allowlist_blocks_unlisted if {
    inp := object.union(base_input, {
        "host": "www.youtube.com",
        "youtube_channel": "@randomchannel",
        "profile": object.union(base_profile, {"allow_youtube_channels": ["@kidsapproved"]}),
    })
    out := policy.decision with input as inp
    out.decision == "block"
    out.reason == "youtube_channel_not_allowed"
}

# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

test_classifier_threshold_blocks if {
    inp := object.union(base_input, {
        "classifier_scores": {"porn": 0.9},
    })
    out := policy.decision with input as inp
    out.decision == "block"
    out.reason == "classifier:porn"
}

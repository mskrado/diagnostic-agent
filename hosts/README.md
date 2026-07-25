# Host overlays
#
# An overlay is a full integration profile directory selected with
# `AGENT_PROFILE_DIR`. Use one when a deployment needs host-specific deltas on
# top of an `examples/` profile (Faro app labels, branded prompts, etc.).
#
# The publishi.ai overlay lives at `hosts/publishi/` in the monorepo only and is
# not published to the public diagnostic-agent repository. Other hosts can add
# `hosts/<name>/` the same way — see `examples/spring-modular-monolith/` for the
# starting point.

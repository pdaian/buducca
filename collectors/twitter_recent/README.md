# Twitter/X recent collector

Stores following-feed posts and DMs separately:
- `workspace/twitter.following.recent`
- `workspace/twitter.dms.recent`

## Multi-account setup
1. Configure `collectors.twitter_recent.accounts`.
2. Each account should define `name`, `following_command`, and `dms_command`.
3. Each command should point to one self-contained script that prints JSON.
3. Run collectors.

Generated workspace files:
- `workspace/twitter.following.recent`
- `workspace/twitter.dms.recent`
- `workspace/collectors/twitter_recent.state.json`

## File structure
- `collectors/twitter_recent/__init__.py`
- `collectors/twitter_recent/README.md`

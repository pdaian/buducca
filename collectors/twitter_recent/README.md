# Twitter/X recent collector

Stores following-feed posts and DMs separately:
- `workspace/twitter.following.recent`
- `workspace/twitter.dms.recent`

## Multi-account setup
1. Configure `collectors.twitter_recent.accounts`.
2. Each account should define `name`, `following_command`, and `dms_command`.
3. Run collectors.

## File structure
- `collectors/twitter_recent/__init__.py`
- `collectors/twitter_recent/README.md`

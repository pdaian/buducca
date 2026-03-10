# llm_based compressor

Runs a prompt-driven compression command for selected files.

## Defaults

- Runs every 24 hours.
- Compresses `workspace/learnings` with a memory-compression prompt that includes current date/time.
- Writes one daily backup to `workspace/learnings.back` before replacing content.

By default it invokes:

```bash
python3 scripts/memory_compressor.py '<JSON payload>'
```

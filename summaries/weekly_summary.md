# ❌ Error Running Summarizer

**Error:** 'SlackSummarizer' object has no attribute 'debug_log'

**Traceback:**
```
Traceback (most recent call last):
  File "/home/runner/work/slack-summarizer/slack-summarizer/slack_summarizer.py", line 383, in <module>
    summarizer = SlackSummarizer()
  File "/home/runner/work/slack-summarizer/slack-summarizer/slack_summarizer.py", line 22, in __init__
    self.log(f"✅ Using model: gemini-2.0-flash-lite")
  File "/home/runner/work/slack-summarizer/slack-summarizer/slack_summarizer.py", line 29, in log
    self.debug_log.append(message)
AttributeError: 'SlackSummarizer' object has no attribute 'debug_log'

```

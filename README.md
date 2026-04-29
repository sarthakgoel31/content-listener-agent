# Content Listener Agent

**Autonomous transcription and intelligence extraction for any video or audio URL.**

## What It Does

Drop a YouTube video, Instagram reel, Spotify podcast, or any media URL into Claude Code and this agent transcribes it, stores the transcript in a local SQLite database, and then summarizes the content and extracts actionable insights -- all without calling a single paid API. It can also process entire YouTube channels in batch and optionally deliver transcripts straight to your Kindle.

## Capabilities

- **Transcribe any URL** -- YouTube videos, Instagram reels, Spotify podcasts, and direct audio/video links. Uses yt-dlp for subtitle extraction (fast path) and falls back to OpenAI Whisper locally when subtitles are unavailable.
- **Batch channel processing** -- Point it at a YouTube channel and transcribe the latest N videos in one command. Useful for research across a creator's entire body of work.
- **Smart model selection** -- Whisper model is chosen based on audio duration to balance speed and accuracy (shorter clips get larger models, longer content gets faster ones).
- **Free summarization** -- Summaries and actionable extraction are performed by Claude Code in-session. No OpenAI API calls, no Anthropic API calls. The LLM doing the summarizing is the one you are already talking to.
- **Persistent storage** -- Every transcript is saved to a local SQLite database, so you can query past transcriptions without re-processing.
- **Kindle delivery** -- Optionally sends formatted transcripts directly to your Kindle via the bundled `send_to_kindle.py` tool.
- **Multi-URL support** -- Process multiple URLs in a single command, get all transcripts back, and ask cross-cutting questions across all of them.

## How to Use

1. Copy `content-listener-agent.md` into your `.claude/agents/` directory.
2. Copy the `tools/send_to_kindle.py` script into `.claude/agents/tools/` (needed for Kindle delivery).
3. The agent requires a local backend server and Python environment at `personal/content-listener/`. See the agent file for server startup instructions.

Then in Claude Code:

```
"Transcribe this video and tell me the key takeaways: https://youtube.com/watch?v=..."
"Listen to the last 5 videos from @channel and summarize what they say about topic X"
"Transcribe this reel and send it to my Kindle"
```

## Architecture

```
User request
    |
    v
[content-listener-agent.md]  -- Claude Code agent orchestrator
    |
    v
[FastAPI server :8420]  -- local backend
    |-- yt-dlp          -- subtitle/audio download
    |-- Whisper          -- local speech-to-text (when no subtitles)
    |-- SQLite DB        -- persistent transcript storage
    v
[Claude Code in-session]  -- summarization + actionable extraction (free)
    |
    v (optional)
[send_to_kindle.py]  -- Gmail SMTP delivery to Kindle
```

The pipeline is deliberately split: transcription is handled by the backend (yt-dlp + Whisper), while summarization and intelligence extraction happen inside the Claude Code session itself. This means zero API cost for the most token-intensive part of the workflow.

## Requirements

- Python 3.10+
- ffmpeg (`brew install ffmpeg`)
- yt-dlp (`pip install yt-dlp`)
- Whisper (`pip install openai-whisper`)

## Built with Claude Code

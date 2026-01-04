# Off-Topic Detection Cog

Detects off-topic discussions using AI and moves them to a designated channel.

## How It Works

1. A user runs `/offtopic` in a channel
2. The bot fetches the last 30 messages (within 3 hours)
3. OpenAI analyzes the messages to find where the conversation went off-topic
4. If off-topic content is found, a summary is posted with voting reactions
5. If enough users vote to approve, the messages are moved to the off-topic channel

## Requirements

- **TransferChannel cog** from AAA3A-cogs (required for moving messages)
- **OpenAI API key**

## Installation

1. Install the TransferChannel cog:
   ```
   [p]repo add AAA3A-cogs https://github.com/AAA3A-AAA3A/AAA3A-cogs
   [p]cog install AAA3A-cogs transferchannel
   [p]load transferchannel
   ```

2. Install this cog:
   ```
   [p]cog install <your-repo> offtopic
   [p]load offtopic
   ```

## Setup

### 1. Set OpenAI API Key
```
[p]set api offtopic openai_api_key,YOUR_API_KEY
```

### 2. Set Off-Topic Destination Channel
```
!offtopic setchannel #off-topic
```

### 3. Set Allowed Role (Optional)
Restrict who can use the `/offtopic` command:
```
!offtopic setrole @Moderator
```
If not set, everyone can use the command.

### 4. Configure Channel Topics
For each channel you want to monitor, set its topic:
```
!offtopic settopic #general
```
The bot will prompt you to describe what the channel is about.

## Usage

### User Command
- `/offtopic` - Analyze recent messages for off-topic content

### Voting
When off-topic content is found:
- React with :thumbsup: to approve moving the messages
- React with :thumbsdown: to dismiss the report
- 5 votes are needed (configurable)
- Voting expires after 5 minutes

## Admin Commands

| Command | Description |
|---------|-------------|
| `!offtopic settings` | View current configuration |
| `!offtopic setchannel #channel` | Set the off-topic destination channel |
| `!offtopic setrole @role` | Set which role can use /offtopic |
| `!offtopic settopic [#channel]` | Set a channel's topic description |
| `!offtopic gettopic [#channel]` | View a channel's topic |
| `!offtopic listtopics` | List all configured topics |
| `!offtopic removetopic #channel` | Remove a channel's topic config |

### Owner-Only Commands

| Command | Description |
|---------|-------------|
| `!offtopic setmodel <model>` | Change AI model (default: gpt-4o) |
| `!offtopic setbaseurl <url>` | Set custom OpenAI API endpoint |

## Configuration Tips

### Writing Good Channel Topics
The better your topic description, the more accurate the detection will be.

**Good examples:**
- "Technical discussions about Python programming, debugging, and code reviews"
- "Gaming news, game recommendations, and discussions about video games"
- "Announcements and updates about server events - no general chat"

**Bad examples:**
- "general"
- "chat"
- "stuff"

## Troubleshooting

### "TransferChannel cog is not installed"
Install it from AAA3A-cogs (see Installation section).

### "API key not configured"
Run `[p]set api offtopic openai_api_key,YOUR_KEY` with your OpenAI API key.

### "No topic configured for this channel"
Run `!offtopic settopic` in the channel and describe what it's for.

### Voting never passes
Check if enough unique users are voting. Bot reactions don't count.

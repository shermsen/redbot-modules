# Off-Topic Detection Cog

Detects off-topic discussions using AI and moves them to a designated channel.

## How It Works

1. A user runs `/offtopic` in a channel
2. The bot fetches the last 30 messages (within 3 hours)
3. OpenAI analyzes the messages to find where the conversation derailed
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

3. Enable and sync slash commands:
   ```
   [p]slash enable offtopic
   [p]slash sync
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
!offtopic addrole @Moderator
```
If not set, everyone can use the command.

### 4. Customize Server Prompt (Optional)
The default prompt is configured for usenet/warez/piracy servers. Customize it for your server:
```
!offtopic setprompt
```

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
| `!offtopic addrole @role` | Add a role that can use /offtopic |
| `!offtopic removerole @role` | Remove a role |
| `!offtopic clearroles` | Allow everyone to use /offtopic |
| `!offtopic setprompt` | Set server-wide detection prompt |
| `!offtopic getprompt` | View current prompt |

### Owner-Only Commands

| Command | Description |
|---------|-------------|
| `!offtopic setmodel <model>` | Change AI model (default: gpt-4o) |
| `!offtopic setbaseurl <url>` | Set custom OpenAI API endpoint |

## How Detection Works

The AI looks for conversations that have completely derailed:
- Unrelated arguments or personal fights
- Extended joke chains that went too far
- Random nonsense unrelated to the server's purpose
- Discussions that have nothing to do with the server topics

Brief jokes or small tangents are ignored - only truly derailed conversations are flagged.

## Troubleshooting

### "TransferChannel cog is not installed"
Install it from AAA3A-cogs (see Installation section).

### "API key not configured"
Run `[p]set api offtopic openai_api_key,YOUR_KEY` with your OpenAI API key.

### Slash command not appearing
Run `[p]slash enable offtopic` then `[p]slash sync`.

### Voting never passes
Check if enough unique users are voting. Bot reactions don't count.

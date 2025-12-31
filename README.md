# AI-Powered Code Review Assistant

Automated GitHub App that reviews pull requests using Claude AI for security, architecture, and code quality analysis.

## Overview

This system solves the problem of inconsistent and time-consuming code reviews by providing automated, AI-powered feedback on every PR. It uses multi-tenant architecture to securely serve multiple teams simultaneously.

## How It Works

**Architecture Flow:**
```
1. GitHub PR Event → Webhook triggers Flask endpoint
2. JWT Authentication → Exchange for installation-specific token
3. Fetch PR Diff → Validate size and parse changes  
4. AI Analysis → Send to Claude with structured prompt
5. Format Response → Post review comment to GitHub
```

**Key Technical Decisions:**

- **JWT + Installation Tokens**: Enables multi-tenant operation where each user's repos are isolated
- **Diff Size Validation**: Prevents token limit errors and controls API costs (50KB limit)
- **Structured Prompting**: Categorizes feedback into security, architecture, performance, and quality
- **Error Handling**: Graceful degradation with retry logic and user-facing error messages
- **Webhook Architecture**: Real-time processing with async handling for scalability

## Security & Scalability

- Multi-tenant isolation via installation-scoped tokens
- Rate limiting to prevent API abuse
- No persistent storage of user code
- Timeout management for long-running requests
- Comprehensive logging for debugging and monitoring

## Tech Stack

- **Backend**: Python, Flask
- **AI**: Anthropic Claude Sonnet 4.5
- **Integration**: GitHub Apps API
- **Deployment**: Render with CD pipeline

## Contact

Shreyan Pasham (https://github.com/shreyan4)

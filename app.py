from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv
from anthropic import Anthropic, APIError, APITimeoutError, RateLimitError

load_dotenv()

app = Flask(__name__)

# Configuration
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

# Constants for limits
MAX_DIFF_SIZE = 50000  # characters
MAX_TOKENS = 4000


@app.route('/webhook/pr', methods=['POST'])
def handle_pr():
    """Main webhook endpoint for GitHub pull requests"""
    
    try:
        event = request.json
        
        # Validate we have the expected data
        if not event:
            return jsonify({'error': 'No JSON payload received'}), 400
        
        action = event.get('action')
        
        # Only process when PR is opened or updated
        if action not in ['opened', 'synchronize']:
            print(f"Ignoring action: {action}")
            return jsonify({'message': f'Ignored action: {action}'}), 200
        
        pr = event.get('pull_request')
        repo = event.get('repository')
        
        if not pr or not repo:
            return jsonify({'error': 'Missing pull_request or repository data'}), 400
        
        pr_number = pr['number']
        repo_full_name = repo['full_name']
        owner = repo['owner']['login']
        repo_name = repo['name']
        
        print(f"Processing PR #{pr_number} in {repo_full_name}")
        
        # Get the code diff
        diff = get_pr_diff(owner, repo_name, pr_number)
        
        # Review with Claude
        review = analyze_code_with_claude(diff)
        
        # Post review back to GitHub
        post_review_to_github(owner, repo_name, pr_number, review)
        
        return jsonify({'message': 'Review posted successfully'}), 200
        
    except ValueError as e:
        # Handle validation errors (diff too large, etc.)
        error_msg = str(e)
        print(f"Validation error: {error_msg}")
        
        # Try to post error message to PR if we have the info
        try:
            if 'pr' in locals() and 'repo' in locals():
                post_error_to_github(
                    repo['owner']['login'],
                    repo['name'],
                    pr['number'],
                    error_msg
                )
        except:
            pass  # If we can't post the error, just log it
            
        return jsonify({'error': error_msg}), 400
        
    except Exception as e:
        # Catch-all for unexpected errors
        error_msg = f"Error processing PR: {str(e)}"
        print(error_msg)
        return jsonify({'error': error_msg}), 500


def get_pr_diff(owner, repo_name, pr_number):
    """Fetch the pull request diff from GitHub REST API with error handling"""

    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"

    headers = {
        "Accept": "application/vnd.github.v3.diff"
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    else:
        print("Warning: No GITHUB_TOKEN set, rate limits will be strict")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        diff = response.text
        
        # Validate diff size
        if len(diff) > MAX_DIFF_SIZE:
            raise ValueError(
                f"Pull request diff is too large ({len(diff)} characters). "
                f"Maximum supported size is {MAX_DIFF_SIZE} characters. "
                f"Please break this PR into smaller changes."
            )
        
        if not diff.strip():
            raise ValueError("Pull request has no code changes to review")
        
        return diff
        
    except requests.exceptions.Timeout:
        raise Exception("GitHub API request timed out. Please try again.")
    
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        if status_code == 401:
            raise Exception("GitHub authentication failed. Check your GITHUB_TOKEN.")
        elif status_code == 403:
            raise Exception("GitHub API rate limit exceeded or insufficient permissions.")
        elif status_code == 404:
            raise Exception(f"Pull request not found: {owner}/{repo_name}#{pr_number}")
        else:
            raise Exception(f"GitHub API error: {status_code} - {e.response.text}")
    
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error while fetching PR: {str(e)}")


def analyze_code_with_claude(diff):
    """Send code diff to Claude for analysis with comprehensive error handling"""

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env file")

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""You are a senior software engineer doing a code review. Analyze this pull request diff and provide:

1. Security Issues: potential vulnerabilities (SQL injection, XSS, auth issues, etc.)
2. Architectural Concerns: design problems, tight coupling, poor separation of concerns.
3. Performance Issues: inefficient algorithms, unnecessary loops, obvious scalability problems.
4. Code Quality: naming, readability, maintainability issues.

Be specific and reference actual code when possible. Focus on meaningful issues, not nitpicks.

Here is the diff:

{diff}

Format your response as a clear, actionable code review in Markdown.
"""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        # Extract text from response
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(block["text"])

        review = "\n".join(text_parts).strip()
        
        if not review:
            raise Exception("Claude returned an empty review")
        
        return review
    
    except RateLimitError:
        raise Exception(
            "Claude API rate limit exceeded. Please wait a moment and try again."
        )
    
    except APITimeoutError:
        raise Exception("Claude API request timed out. Please try again.")
    
    except APIError as e:
        # Handle specific API errors
        error_message = str(e)
        if "credit" in error_message.lower() or "balance" in error_message.lower():
            raise Exception(
                "Insufficient Anthropic API credits. Please add credits at console.anthropic.com"
            )
        elif "invalid" in error_message.lower() and "api" in error_message.lower():
            raise Exception("Invalid Anthropic API key. Check your ANTHROPIC_API_KEY in .env")
        else:
            raise Exception(f"Claude API error: {error_message}")
    
    except Exception as e:
        # Catch any other unexpected errors
        raise Exception(f"Unexpected error calling Claude API: {str(e)}")


def post_review_to_github(owner, repo, pr_number, review_text):
    """Post the review as a comment on the PR with error handling"""
    
    if not GITHUB_TOKEN:
        raise RuntimeError("Cannot post review: GITHUB_TOKEN is not set")
    
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews'
    
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    data = {
        'body': f"## 🤖 AI Code Review\n\n{review_text}",
        'event': 'COMMENT'
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        print(f"✓ Review posted successfully to PR #{pr_number}")
        
    except requests.exceptions.Timeout:
        raise Exception("Timeout while posting review to GitHub")
    
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        if status_code == 401:
            raise Exception("GitHub authentication failed when posting review")
        elif status_code == 403:
            raise Exception("Insufficient permissions to post review. Check token scopes.")
        elif status_code == 404:
            raise Exception(f"Cannot post review: PR {owner}/{repo}#{pr_number} not found")
        elif status_code == 422:
            raise Exception("Invalid review data. The PR may be closed or locked.")
        else:
            raise Exception(f"GitHub API error when posting review: {status_code}")
    
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error posting review: {str(e)}")


def post_error_to_github(owner, repo, pr_number, error_message):
    """Post an error message to the PR when review fails"""
    
    if not GITHUB_TOKEN:
        return  # Silently fail if no token
    
    url = f'https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments'
    
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    data = {
        'body': f"## ⚠️ AI Code Review Failed\n\n{error_message}\n\n"
                f"Please check the webhook logs or contact the maintainer."
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        print(f"✓ Error message posted to PR #{pr_number}")
    except Exception as e:
        print(f"✗ Failed to post error message: {str(e)}")
        # Don't raise - this is a best-effort notification


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
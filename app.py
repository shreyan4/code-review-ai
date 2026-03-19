from flask import Flask, request, jsonify
import requests
import os
import jwt
import time
import threading
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

app = Flask(__name__)

# Configuration
GITHUB_APP_ID = os.getenv('GITHUB_APP_ID')
GITHUB_PRIVATE_KEY = os.getenv('GITHUB_PRIVATE_KEY').replace('\\n', '\n')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

MAX_DIFF_SIZE = 50000
MAX_TOKENS = 4000


def generate_jwt():
    """Generate JWT for GitHub App authentication"""
    payload = {
        'iat': int(time.time()),
        'exp': int(time.time()) + (10 * 60),  # 10 minutes
        'iss': GITHUB_APP_ID
    }
    
    jwt_token = jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm='RS256')
    return jwt_token


def get_installation_token(installation_id):
    """Get an installation access token for a specific installation"""
    jwt_token = generate_jwt()
    
    url = f'https://api.github.com/app/installations/{installation_id}/access_tokens'
    headers = {
        'Authorization': f'Bearer {jwt_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    response = requests.post(url, headers=headers)
    response.raise_for_status()
    
    return response.json()['token']


@app.route('/test-auth')
def test_auth():
    try:
        token = generate_jwt()
        url = 'https://api.github.com/app'
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        response = requests.get(url, headers=headers)
        return jsonify({
            'status': response.status_code,
            'response': response.json()
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500



@app.route('/webhook/pr', methods=['GET', 'POST'])
def handle_pr():
    if request.method == 'GET':
        return jsonify({'message': 'Webhook endpoint is working. Send POST requests here.'}), 200

    event = request.json
    if not event:
        return jsonify({'error': 'No JSON payload received'}), 400

    # Start processing in background thread
    thread = threading.Thread(target=process_pr, args=(event,))
    thread.start()

    # Return immediately to GitHub
    return jsonify({'message': 'Review queued'}), 200


def process_pr(event):
    """Process PR review in background"""
    try:
        action = event.get('action')
        if action not in ['opened', 'synchronize']:
            return

        pr = event.get('pull_request')
        repo = event.get('repository')
        installation = event.get('installation')

        if not pr or not repo or not installation:
            return

        installation_token = get_installation_token(installation['id'])
        pr_number = pr['number']
        owner = repo['owner']['login']
        repo_name = repo['name']

        print(f"Processing PR #{pr_number} in {repo['full_name']}")

        diff = get_pr_diff(owner, repo_name, pr_number, installation_token)
        review = analyze_code_with_claude(diff)
        post_review_to_github(owner, repo_name, pr_number, review, installation_token)

    except Exception as e:
        import traceback
        print(f"Error processing PR: {str(e)}")
        print(traceback.format_exc())


def get_pr_diff(owner, repo_name, pr_number, token):
    """Fetch the pull request diff from GitHub REST API with error handling"""

    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"

    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "Authorization": f"token {token}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        diff = response.text
        
        print(f"📊 Diff size: {len(diff)} characters")
        
        if len(diff) > MAX_DIFF_SIZE:
            raise ValueError(
                f"Pull request diff is too large ({len(diff)} characters). "
                f"Maximum supported size is {MAX_DIFF_SIZE} characters."
            )
        
        if not diff.strip():
            raise ValueError("Pull request has no code changes to review")
        
        return diff
        
    except Exception as e:
        raise Exception(f"Error fetching diff: {str(e)}")


def analyze_code_with_claude(diff):
    """Send code diff to Claude for analysis"""

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

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
    
    except Exception as e:
        raise Exception(f"Claude API error: {str(e)}")


@app.route('/test-comment')
def test_comment():
    """Test if we can post a comment"""
    try:
        # Replace these with your actual values
        owner = "shreyan4"  # e.g., "johnsmith"
        repo = "codereviewtest"  # e.g., "test-repo"
        pr_number = 5  # Your latest test PR number
        installation_id = 101785818  # Replace with your installation ID from Step 3
        
        token = get_installation_token(installation_id)
        
        url = f'https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments'
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        data = {'body': '🧪 Test comment from GitHub App'}
        
        response = requests.post(url, json=data, headers=headers)
        return jsonify({
            'status': response.status_code,
            'response': response.json() if response.ok else response.text
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

def post_review_to_github(owner, repo, pr_number, review_text, token):
    """Post the review as a comment on the PR"""
    
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews'
    
    headers = {
        'Authorization': f'token {token}',
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
        
    except Exception as e:
        raise Exception(f"Error posting review: {str(e)}")


@app.route('/')
def home():
    return "AI Code Review Assistant is running!", 200


@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
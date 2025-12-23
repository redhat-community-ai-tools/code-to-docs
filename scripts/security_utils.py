"""
Security utilities for code-to-docs GitHub Action

This module provides security functions to prevent:
- Credential leakage in error messages
- Path traversal attacks
- Unauthorized file access
"""

import os
import subprocess
from pathlib import Path


def sanitize_output(text, sensitive_tokens=None):
    """
    Remove sensitive tokens from text output.
    
    Args:
        text: Text to sanitize
        sensitive_tokens: Additional sensitive tokens to remove
    
    Returns:
        str: Sanitized text with tokens replaced by ***TOKEN***
    """
    if not text:
        return text
    
    if sensitive_tokens is None:
        sensitive_tokens = []
    
    # Add GH_TOKEN to sensitive list
    gh_token = os.environ.get("GH_TOKEN", "")
    if gh_token:
        sensitive_tokens.append(gh_token)
    
    # Add GEMINI_API_KEY to sensitive list
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        sensitive_tokens.append(gemini_key)
    
    # Replace all sensitive tokens
    sanitized = text
    for token in sensitive_tokens:
        if token and len(token) > 0:
            sanitized = sanitized.replace(token, "***TOKEN***")
    
    return sanitized


def run_command_safe(cmd, check=False, capture_output=True, **kwargs):
    """
    Run subprocess command with sanitized output.
    
    Args:
        cmd: Command to run as list
        check: Raise exception on non-zero return code
        capture_output: Capture stdout and stderr
        **kwargs: Additional subprocess.run arguments
    
    Returns:
        CompletedProcess: Result with sanitized output
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            **kwargs
        )
        
        if check and result.returncode != 0:
            # Sanitize error output before raising
            sanitized_stderr = sanitize_output(result.stderr)
            sanitized_stdout = sanitize_output(result.stdout)
            raise subprocess.CalledProcessError(
                result.returncode,
                cmd,
                output=sanitized_stdout,
                stderr=sanitized_stderr
            )
        
        return result
    except subprocess.CalledProcessError as e:
        # Re-raise with sanitized output
        e.stderr = sanitize_output(str(e.stderr)) if e.stderr else None
        e.stdout = sanitize_output(str(e.stdout)) if e.stdout else None
        raise
    except Exception as e:
        # Sanitize any other exceptions that might contain tokens
        e.args = tuple(sanitize_output(str(arg)) for arg in e.args)
        raise


def validate_file_path(file_path, base_dir=None):
    """
    Validate file path is within allowed directory.
    
    Args:
        file_path: Path to validate
        base_dir: Base directory (defaults to current directory)
    
    Returns:
        bool: True if safe, False otherwise
    """
    if base_dir is None:
        base_dir = Path.cwd()
    else:
        base_dir = Path(base_dir)
    
    try:
        file_path = Path(file_path)
        # Resolve to absolute path
        resolved = (base_dir / file_path).resolve()
        
        # Check if it's within base directory
        resolved.relative_to(base_dir.resolve())
        
        return True
    except (ValueError, OSError):
        return False


def setup_git_credentials(token, repo_url):
    """
    Setup git credential helper for secure authentication.
    
    Args:
        token: GitHub token
        repo_url: Repository URL
    
    Returns:
        bool: True if successful, False otherwise
    """
    # Use git config to set credential helper
    try:
        helper_cmd = f"!f() {{ echo 'username=x-access-token'; echo 'password={token}'; }}; f"
        
        run_command_safe([
            "git", "config", "credential.helper",
            helper_cmd
        ], check=False)
        return True
    except subprocess.CalledProcessError as e:
        # Don't print the command (contains token) - only print exit code
        print(f"Warning: Git credential setup failed with exit code {e.returncode}")
        return False
    except Exception as e:
        # Generic exception - sanitize just in case
        error_msg = sanitize_output(str(e))
        print(f"Warning: Could not setup git credentials: {error_msg}")
        return False


def validate_docs_file_extension(file_path):
    """
    Validate file has allowed documentation extension.
    
    Args:
        file_path: Path to check
    
    Returns:
        bool: True if .adoc, .md, or .rst, False otherwise
    """
    return file_path.endswith('.adoc') or file_path.endswith('.md') or file_path.endswith('.rst')


def validate_docs_subfolder(subfolder_path):
    """
    Validate docs subfolder path is safe.
    
    Args:
        subfolder_path: Subfolder path to validate
    
    Returns:
        bool: True if safe, False otherwise
    """
    if not subfolder_path:
        return True
    
    # Security: ensure no path traversal
    if ".." in subfolder_path or subfolder_path.startswith("/"):
        return False
    
    return True


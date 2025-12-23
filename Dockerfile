FROM registry.access.redhat.com/ubi9/python-311:latest

# Switch to root user for package installation
USER root

# Install system dependencies (git and curl-minimal already included in base image)
RUN dnf update -y && dnf clean all

# Install GitHub CLI and jq for JSON parsing
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo | tee /etc/yum.repos.d/github-cli.repo \
    && dnf install -y gh jq

# Install Python dependencies
RUN pip install --no-cache-dir -U google-genai

# Set up working directory
WORKDIR /app

# Copy the scripts
COPY scripts/suggest_docs.py /app/suggest_docs.py
COPY scripts/security_utils.py /app/security_utils.py

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set git config for commits
RUN git config --global user.email "action@github.com" && \
    git config --global user.name "GitHub Action"

ENTRYPOINT ["/entrypoint.sh"]

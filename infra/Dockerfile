FROM condaforge/miniforge3

WORKDIR /app

ENV CONDA_ENV_PATH /opt/conda/envs/TGHandyUtils
ENV PATH $CONDA_ENV_PATH/bin:$PATH

# Install system libraries required for compilation + node (for the claude CLI backend)
RUN apt-get update && \
    apt-get install -y curl gcc g++ libgl1-mesa-dev nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# claude -p console backend (routing key 'claude-p' in anki role models).
# Auth comes from CLAUDE_CODE_OAUTH_TOKEN at runtime (`claude setup-token` on the host) —
# never baked into the image.
RUN npm install -g @anthropic-ai/claude-code @openai/codex

# Create necessary directories
RUN mkdir -p /app/data/db /app/data/logs

# Copy environment.yml first for better layer caching
COPY ./environment.yml /app/environment.yml
COPY ./packages/ai_workflow_engine /app/packages/ai_workflow_engine
COPY ./packages/ai_workflow_tools /app/packages/ai_workflow_tools
COPY ./packages/ai_workflow_viewer /app/packages/ai_workflow_viewer

# Create the Conda environment and install dependencies from environment.yml
RUN mamba env create -f environment.yml

# Copy the rest of the application
COPY . .

ENV PATH="/opt/conda/envs/TGHandyUtils/bin:$PATH"
ENV CONDA_DEFAULT_ENV=TGHandyUtils

CMD ["python", "main.py"]

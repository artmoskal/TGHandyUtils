FROM condaforge/miniforge3

WORKDIR /app

ENV CONDA_ENV_PATH /opt/conda/envs/TGHandyUtils
ENV PATH $CONDA_ENV_PATH/bin:$PATH

# Install system libraries required for compilation
RUN apt-get update && \
    apt-get install -y curl gcc g++ libgl1-mesa-dev && \
    rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /app/data/db /app/data/logs

# Copy environment.yml first for better layer caching
COPY ./environment.yml /app/environment.yml
COPY ./packages/ai_workflow_engine /app/packages/ai_workflow_engine

# Create the Conda environment and install dependencies from environment.yml
RUN mamba env create -f environment.yml

# Copy the rest of the application
COPY . .

ENV PATH="/opt/conda/envs/TGHandyUtils/bin:$PATH"
ENV CONDA_DEFAULT_ENV=TGHandyUtils

CMD ["python", "main.py"]

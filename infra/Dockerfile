FROM continuumio/miniconda3

WORKDIR /app

ENV CONDA_ENV_PATH /opt/conda/envs/TGHandyUtils
ENV PATH $CONDA_ENV_PATH/bin:$PATH

# Update conda and install tools via conda
RUN conda update -n base -c defaults conda -y && \
    conda config --add channels conda-forge && \
    conda config --set channel_priority flexible

# Install system libraries required for OpenCV
RUN apt-get update && \
    apt-get install -y curl gcc g++ libgl1-mesa-dev && \
    rm -rf /var/lib/apt/lists/* && \
    conda install libarchive mamba gcc_linux-64 gxx_linux-64 -c conda-forge

# Create necessary directories
RUN mkdir -p /app/data/db /app/data/logs

# Copy environment.yml first for better layer caching
COPY ./environment.yml /app/environment.yml

# Create the Conda environment and install dependencies from environment.yml
RUN mamba create -n TGHandyUtils python=3.10 && \
    mamba env update -n TGHandyUtils -f environment.yml

# Copy the rest of the application
COPY . .

ENV PATH="/opt/conda/envs/TGHandyUtils/bin:$PATH"
ENV CONDA_DEFAULT_ENV=TGHandyUtils

CMD ["python", "main.py"]
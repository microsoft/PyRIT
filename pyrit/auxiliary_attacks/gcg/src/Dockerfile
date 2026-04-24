FROM mcr.microsoft.com/azureml/curated/acpt-pytorch-2.2-cuda12.1:37

RUN apt-get update

ENV UV_ENVIRONMENT_PATH /azureml-envs/pyrit

# Prepend path to AzureML environment
ENV PATH $UV_ENVIRONMENT_PATH/bin:$PATH

# Create uv environment
RUN uv venv --python 3.11 && \
    uv pip cache purge

RUN uv pip list

RUN git clone https://github.com/microsoft/PyRIT.git

RUN cd PyRIT && uv pip install -extra dev --extra gcg && uv pip install git+https://github.com/lm-sys/FastChat.git@2c68a13bfe10b86f40e3eefc3fcfacb32c00b02a
RUN uv pip freeze

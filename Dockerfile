# FlakeHunter sandbox image.
#
# This container IS the isolation boundary: the orchestrator, the agent, and
# every execution of agent-authored code all run inside it. Nothing the agent
# writes can reach the host except through the explicitly mounted results/
# directory, and only after a human has approved it.
FROM python:3.11.9-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    FLAKEHUNTER_SCRATCH=/scratch

# Marker file. src/sandbox/executor.py refuses to execute anything unless this
# exists, which makes "consequential actions run in a sandbox" a property the
# code enforces rather than a claim the README makes.
RUN printf 'flakehunter-sandbox\n' > /.flakehunter-sandbox

RUN groupadd --gid 1000 runner \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash runner \
 && mkdir -p /scratch /app \
 && chown runner:runner /scratch /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
USER runner

CMD ["python", "-m", "pytest", "tests", "-q"]

# Preserve the running worker's dependencies while replacing its application with the Git release.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN rm -rf /app/app
COPY --chown=appuser:appuser app/ /app/app/
USER appuser

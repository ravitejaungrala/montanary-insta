FROM public.ecr.aws/lambda/python:3.11

# Install serif fonts for PIL compositor (Agent Post template rendering).
# Without these, PIL falls back to a tiny 10pt bitmap font and the editorial
# headline text on templates becomes microscopic (~4px tall instead of ~60px).
#
# The Lambda python:3.11 base image is Amazon Linux 2 (AL2) which uses `yum`.
# DejaVu fonts are available in the AL2 default repos. Liberation fonts are
# NOT in the default AL2 repos and would need EPEL — we skip them since
# DejaVu Serif is a fine editorial serif on its own.
RUN yum install -y \
        dejavu-serif-fonts \
        dejavu-sans-fonts \
    && yum clean all \
    && rm -rf /var/cache/yum

# Copy requirements and install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . ${LAMBDA_TASK_ROOT}

# Static uploads directory (ephemeral in Lambda /tmp)
RUN mkdir -p /tmp/uploads

# Sanity-check fonts are reachable at expected paths — advisory only
# (doesn't fail the build, but logs what's found during image build)
RUN echo "=== Installed fonts ===" \
    && find /usr/share/fonts -type f \( -name "*Serif*.ttf" -o -name "*Sans*.ttf" -o -name "*.ttf" \) 2>/dev/null | head -30 \
    && echo "=== end fonts list ==="

# Lambda expects the handler in this format
CMD ["lambda_handler.handler"]

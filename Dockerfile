FROM python:3.12-slim

# Install system dependencies
# gosu is used for PUID/PGID support (running as non-root user)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    gosu \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp (latest version); pick the right binary for the host arch
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then \
        YT_DLP_BIN="yt-dlp_linux_aarch64"; \
    else \
        YT_DLP_BIN="yt-dlp"; \
    fi && \
    curl -L "https://github.com/yt-dlp/yt-dlp/releases/latest/download/${YT_DLP_BIN}" \
        -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi~=0.128.0 \
    uvicorn[standard]~=0.40.0 \
    httpx~=0.28.1 \
    pydantic~=2.12.5 \
    mutagen~=1.47.0 \
    playwright~=1.58.0 \
    apprise~=1.9.3 \
    bcrypt~=4.2.0

# SeleniumBase is kept beside Playwright rather than replacing the working
# Spotify/Amazon scrapers. It is used solely for Monochrome's browser Turnstile
# exchange; Chrome's audio traffic never passes through WebDriver.
RUN pip install --no-cache-dir seleniumbase~=4.49.0

# Install Playwright browsers into a fixed path so non-root users (PUID/PGID) can find them.
# Without this, Playwright falls back to ~/.cache/ms-playwright which resolves differently per user.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium --with-deps

# Playwright installs Xvfb, but SeleniumBase's virtual display also needs xauth.
# Its PyAutoGUI interaction helper loads the official Python image's _tkinter
# extension, whose Tcl/Tk runtime libraries are deliberately absent in slim.
RUN apt-get update && apt-get install -y --no-install-recommends xauth tk8.6 && \
    rm -rf /var/lib/apt/lists/*

# UC/CDP mode requires headed Google Chrome under Xvfb; true headless mode is
# intentionally detectable. Debian Chromium is the ARM fallback, where Google
# does not publish a Linux stable .deb.
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
        curl -L https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
            -o /tmp/google-chrome.deb && \
        apt-get update && apt-get install -y --no-install-recommends /tmp/google-chrome.deb && \
        rm -f /tmp/google-chrome.deb; \
    else \
        apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Avoid runtime driver downloads. Google publishes the UC driver used by
# SeleniumBase for amd64; on ARM, seed its driver slot from Debian's matching
# Chromium package instead of downloading an unusable linux64 binary.
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
        sbase get uc_driver stable; \
    else \
        cp /usr/bin/chromedriver \
            /usr/local/lib/python3.12/site-packages/seleniumbase/drivers/uc_driver && \
        chmod a+rwx /usr/local/lib/python3.12/site-packages/seleniumbase/drivers/uc_driver; \
    fi

# Create app directory
WORKDIR /app

# Copy application files
COPY *.py /app/
COPY static /app/static/
COPY entrypoint.sh /app/

# Create data directory for SQLite
RUN mkdir -p /data

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose port (default; override with LISTEN_PORT env var)
EXPOSE 8080

# Health check - uses LISTEN_PORT if set, falls back to 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${LISTEN_PORT:-8080}/ || exit 1

# Run the application
CMD ["/app/entrypoint.sh"]

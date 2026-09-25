#!/bin/sh
# Sites change constantly and yt-dlp follows them, so pull the latest
# release on every start. A failed update (no network) is not fatal.
if [ "${YTDLP_AUTO_UPDATE:-1}" = "1" ]; then
    pip install --no-cache-dir --quiet --upgrade "yt-dlp[default]" bgutil-ytdlp-pot-provider \
        || echo "yt-dlp update failed, using the bundled version"
fi
echo "yt-dlp $(yt-dlp --version)"
exec "$@"

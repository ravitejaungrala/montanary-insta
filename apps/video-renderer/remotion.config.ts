import { Config } from '@remotion/cli/config';

/**
 * Remotion build configuration.
 *
 * - Output: H.264 MP4 — universally supported by every social platform
 *   we publish to (LinkedIn, X, Instagram, Facebook).
 * - Bitrate: 8 Mbps for 1080p so the file is high quality but still
 *   under each platform's per-post upload ceiling.
 * - Concurrency: default (Remotion picks based on cpu cores). Override
 *   only if local previews are too slow on a low-end laptop.
 */
Config.setVideoImageFormat('jpeg');
Config.setCodec('h264');
Config.setPixelFormat('yuv420p');
Config.setOverwriteOutput(true);

// Set chromium flag for any odd headless rendering edge cases.
Config.setChromiumOpenGlRenderer('angle');

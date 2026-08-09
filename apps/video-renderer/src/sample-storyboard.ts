import { staticFile } from 'remotion';
import type { ReelPromoStoryboard } from './storyboard-types';

/**
 * v4 character-driven sample.
 *
 * The avatar URLs in this sample reference free Unsplash CDN photos so
 * the local preview shows real people without needing the backend
 * image-generation step to run. Production storyboards from the agent
 * will populate `avatar_url` with Gemini-generated S3 URLs instead.
 */
export const sampleStoryboard: ReelPromoStoryboard = {
  template: 'reel_promo',
  aspect_ratio: '9:16',
  brand: {
    primary_color: '#ff4500',
    secondary_color: '#0f172a',
    background_color: '#ffffff',
    text_color: '#0f172a',
    logo_url: staticFile('pipelyt-logo.png'),
    company_name: 'Spenzo AI',
    tagline: 'AI Powered Marketing Intelligence Platform',
  },
  scenes: [
    {
      type: 'logo_intro',
      duration_sec: 2,
    },
    {
      type: 'pain_avatar',
      duration_sec: 8,
      avatar_prompt: 'Mid-30s marketing manager at a cluttered desk, stressed expression, holding head with both hands, laptop screen glowing with dashboards in foreground, dim warm office light, photorealistic editorial photography, 9:16 vertical, shallow depth of field',
      // Demo URL (Unsplash CDN — real photo of a stressed person at desk).
      avatar_url: 'https://images.unsplash.com/photo-1521791136064-7986c2920216?auto=format&fit=crop&w=1080&h=1920&q=80',
      label: 'The Problem',
      quote: 'I waste 30% of my marketing budget every quarter and have no idea which channels actually work.',
    },
    {
      type: 'solution_avatar',
      duration_sec: 12,
      avatar_prompt: 'Confident 30s marketing strategist, professional attire, smiling, modern office with floor-to-ceiling windows, holding tablet showing clean dashboard, golden-hour natural light, photorealistic editorial photography, 9:16 vertical',
      avatar_url: 'https://images.unsplash.com/photo-1573497019940-1c28c88b4f3e?auto=format&fit=crop&w=1080&h=1920&q=80',
      label: 'The Solution',
      quote: 'Spenzo AI rebalances your spend in real time across every channel and shows you exactly which ones drive revenue.',
      proof: 'Save 60% on wasted spend',
    },
    {
      type: 'cta_card',
      duration_sec: 8,
      headline: 'See It In Action',
      subline: 'spenzo.ai',
    },
  ],
};

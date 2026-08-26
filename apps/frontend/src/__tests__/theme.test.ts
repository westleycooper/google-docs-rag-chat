/**
 * The theme registry and its persistence.
 *
 * loadStoredThemeId/storeThemeId wrap localStorage defensively -- a private
 * browsing tab that throws on access must degrade to the default rather than
 * crash the app, which is exactly the kind of edge case easy to skip until it
 * happens to a real user.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buildTheme,
  DEFAULT_THEME_ID,
  loadStoredThemeId,
  storeThemeId,
  THEME_IDS,
  THEME_PRESETS,
} from '../theme';

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('theme registry', () => {
  it('defaults to Retro Teal', () => {
    expect(DEFAULT_THEME_ID).toBe('retroTeal');
  });

  it('lists every preset id exactly once', () => {
    expect(new Set(THEME_IDS).size).toBe(THEME_IDS.length);
    expect(THEME_IDS.sort()).toEqual(Object.keys(THEME_PRESETS).sort());
  });

  it('builds a resolvable MUI theme for every preset', () => {
    for (const id of THEME_IDS) {
      const theme = buildTheme(id);
      expect(theme.palette.primary.main).toBeTruthy();
      expect(theme.contextColours.retrieved).toBeTruthy();
      expect(theme.statusColours.ok).toBeTruthy();
    }
  });

  it('only Retro Teal carries the line-gradient treatment', () => {
    expect(buildTheme('retroTeal').bodyBackgroundImage).toContain('gradient');
    expect(buildTheme('retroTeal').appBarBackground).toContain('gradient');
    expect(buildTheme('wesAndersonLight').bodyBackgroundImage).toBeUndefined();
    expect(buildTheme('wesAndersonDark').appBarBackground).toBeUndefined();
  });

  it('ties secondary to the retrieved context colour in every preset', () => {
    for (const id of THEME_IDS) {
      const theme = buildTheme(id);
      expect(theme.palette.secondary?.main).toBe(theme.contextColours.retrieved);
    }
  });
});

describe('theme persistence', () => {
  it('falls back to the default when nothing is stored', () => {
    expect(loadStoredThemeId()).toBe(DEFAULT_THEME_ID);
  });

  it('round-trips a stored choice', () => {
    storeThemeId('wesAndersonDark');
    expect(loadStoredThemeId()).toBe('wesAndersonDark');
  });

  it('ignores a value that is not a known theme id', () => {
    window.localStorage.setItem('ragdrive:theme', 'not-a-real-theme');
    expect(loadStoredThemeId()).toBe(DEFAULT_THEME_ID);
  });

  it('falls back to the default when localStorage throws on read', () => {
    vi.spyOn(window.localStorage.__proto__, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked');
    });
    expect(loadStoredThemeId()).toBe(DEFAULT_THEME_ID);
  });

  it('does not throw when localStorage.setItem fails', () => {
    vi.spyOn(window.localStorage.__proto__, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked');
    });
    expect(() => storeThemeId('retroTeal')).not.toThrow();
  });
});

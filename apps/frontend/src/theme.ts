import { createTheme, type PaletteOptions, type ThemeOptions } from '@mui/material/styles';

/**
 * Multiple named themes rather than a single light/dark toggle. Each preset
 * carries not just an MUI palette but the app's own colour vocabulary --
 * CONTEXT_COLOURS and STATUS_COLOURS -- so a preset switch retints the context
 * meter, citation chips and status indicators along with the chrome, rather
 * than leaving them stuck on whichever theme was active when the app first
 * loaded. That's carried on the MUI theme object itself (module augmentation
 * below) rather than as a separate React context, so any component that
 * already calls `useTheme()` gets the current preset's colours for free.
 */

export type ThemeId =
  | 'consoleLight'
  | 'consoleDark'
  | 'retroTeal'
  | 'wesAndersonLight'
  | 'wesAndersonDark';

export const DEFAULT_THEME_ID: ThemeId = 'consoleLight';

export interface ContextColours {
  system: string;
  pinned: string;
  history: string;
  retrieved: string;
}

export interface StatusColours {
  ok: string;
  degraded: string;
  down: string;
  unknown: string;
}

interface ThemePreset {
  label: string;
  palette: PaletteOptions;
  contextColours: ContextColours;
  statusColours: StatusColours;
  /** CSS `background-image` for the AppBar. Absent = flat `background.paper`. */
  appBarBackground?: string;
  /** CSS `background-image` for the page body, behind every surface. */
  bodyBackgroundImage?: string;
}

// Module augmentation: teaches MUI's Theme/ThemeOptions types about the two
// custom colour vocabularies and the gradient hooks, so `useTheme()` and
// `createTheme()` both see them without an `as any` anywhere in the app.
declare module '@mui/material/styles' {
  interface Theme {
    contextColours: ContextColours;
    statusColours: StatusColours;
    appBarBackground?: string | undefined;
    bodyBackgroundImage?: string | undefined;
  }
  // Optional here (unlike Theme above): ThemeOptions is the partial input to
  // createTheme, and `shared` -- the base building block every preset spreads
  // over -- supplies neither field itself.
  interface ThemeOptions {
    contextColours?: ContextColours | undefined;
    statusColours?: StatusColours | undefined;
    appBarBackground?: string | undefined;
    bodyBackgroundImage?: string | undefined;
  }
}

// Modelled on Anthropic's own console (API keys / usage / billing): a flat,
// near-white admin UI with a single muted teal accent and dark slate-navy for
// the primary action, rather than a saturated brand colour doing all the
// work. No gradients or textures -- the whole point of this preset is to look
// like restrained enterprise software, not a themed app.
const CONSOLE_LIGHT: ThemePreset = {
  label: 'Console (Light)',
  palette: {
    mode: 'light',
    background: { default: '#F7F7F5', paper: '#FFFFFF' },
    primary: { main: '#1C2B33' }, // dark slate-navy -- the filled CTA colour
    secondary: { main: '#4F7C78' }, // muted teal -- nav selection / accents
    error: { main: '#B4322F' },
    warning: { main: '#B8860B' },
    success: { main: '#3F7D52' },
    info: { main: '#3E6B99' },
    text: { primary: '#1F2D36', secondary: '#5C6A72' },
  },
  contextColours: {
    system: '#7A8790',
    pinned: '#9C3D33',
    history: '#3E6B99',
    retrieved: '#4F7C78',
  },
  statusColours: {
    ok: '#3F7D52',
    degraded: '#B8860B',
    down: '#B4322F',
    unknown: '#7A8790',
  },
};

const CONSOLE_DARK: ThemePreset = {
  label: 'Console (Dark)',
  palette: {
    mode: 'dark',
    background: { default: '#12181A', paper: '#1A2224' },
    // The light preset's CTA navy would nearly vanish against a dark
    // background, so the roles swap emphasis: the teal carries the primary
    // action here, brightened enough to read, and slate-blue takes the
    // secondary/accent role the navy held in the light version.
    primary: { main: '#5FA39D' },
    secondary: { main: '#7C93A0' },
    error: { main: '#E0685F' },
    warning: { main: '#D9A441' },
    success: { main: '#6FBF86' },
    info: { main: '#6FA8D9' },
    text: { primary: '#EDF2F1', secondary: '#9FB0B3' },
  },
  contextColours: {
    system: '#8A97A0',
    pinned: '#C97A52',
    history: '#6FA8D9',
    retrieved: '#7C93A0',
  },
  statusColours: {
    ok: '#6FBF86',
    degraded: '#D9A441',
    down: '#E0685F',
    unknown: '#8A97A0',
  },
};

const RETRO_TEAL: ThemePreset = {
  label: 'Retro Teal',
  palette: {
    mode: 'dark',
    // Near-black teal rather than neutral black, so the whole UI sits inside
    // one hue family instead of hue-neutral chrome with teal accents on top.
    background: { default: '#071613', paper: '#0D211C' },
    primary: { main: '#2DD4BF' },
    // Tied to contextColours.retrieved, same coupling as the other presets:
    // the app's busiest action colour and "what retrieval found this turn"
    // share one colour rather than competing.
    secondary: { main: '#2DD4BF' },
    error: { main: '#F97362' }, // retro coral -- warmth against an all-teal field
    warning: { main: '#FBBF24' }, // CRT amber
    success: { main: '#34D399' },
    info: { main: '#22D3EE' },
    text: { primary: '#E4FFFB', secondary: '#8FBDB4' },
  },
  contextColours: {
    system: '#3A5049',
    pinned: '#F97362', // coral pop for "explicitly kept," against an otherwise cool field
    history: '#2E7D74',
    retrieved: '#2DD4BF',
  },
  statusColours: {
    ok: '#34D399',
    degraded: '#FBBF24',
    down: '#F97362',
    unknown: '#3A5049',
  },
  appBarBackground: 'linear-gradient(90deg, #071613 0%, #0F3A33 50%, #071613 100%)',
  // Thin horizontal teal scanlines at low opacity: a retro CRT/vector-terminal
  // texture behind every surface, subtle enough not to fight body text.
  bodyBackgroundImage:
    'repeating-linear-gradient(180deg, rgba(45, 212, 191, 0.05) 0px, ' +
    'rgba(45, 212, 191, 0.05) 1px, transparent 1px, transparent 6px)',
};

const WES_ANDERSON_LIGHT: ThemePreset = {
  label: 'Wes Anderson (Light)',
  palette: {
    mode: 'light',
    background: { default: '#F3E9D8', paper: '#FBF6EC' }, // warm cream, hotel lobby
    primary: { main: '#3D7A79' },
    secondary: { main: '#C08A2E' },
    error: { main: '#A23B3B' },
    warning: { main: '#C08A2E' },
    success: { main: '#5B7A4A' },
    info: { main: '#4F7C8A' },
    text: { primary: '#3A2E28', secondary: '#6B5D4F' },
  },
  contextColours: {
    system: '#6E5F4C',
    pinned: '#8B3A42',
    history: '#4F7C8A',
    retrieved: '#C08A2E',
  },
  statusColours: {
    ok: '#5B7A4A',
    degraded: '#C08A2E',
    down: '#A23B3B',
    unknown: '#6E5F4C',
  },
};

const WES_ANDERSON_DARK: ThemePreset = {
  label: 'Wes Anderson (Dark)',
  palette: {
    mode: 'dark',
    background: { default: '#241417', paper: '#331C20' }, // velvet maroon, lit interior
    primary: { main: '#6FB3AC' },
    secondary: { main: '#C08A2E' },
    error: { main: '#D2603A' },
    warning: { main: '#E3B23C' },
    success: { main: '#8FBF7A' },
    info: { main: '#8FC1D1' },
    text: { primary: '#F2E8D5', secondary: '#C9B79E' },
  },
  contextColours: {
    system: '#6E5F4C',
    pinned: '#8B3A42',
    history: '#4F7C8A',
    retrieved: '#C08A2E',
  },
  statusColours: {
    ok: '#5B7A4A',
    degraded: '#C08A2E',
    down: '#A23B3B',
    unknown: '#6E5F4C',
  },
};

export const THEME_PRESETS: Record<ThemeId, ThemePreset> = {
  consoleLight: CONSOLE_LIGHT,
  consoleDark: CONSOLE_DARK,
  retroTeal: RETRO_TEAL,
  wesAndersonLight: WES_ANDERSON_LIGHT,
  wesAndersonDark: WES_ANDERSON_DARK,
};

/** Order the picker lists them in. */
export const THEME_IDS: ThemeId[] = [
  'consoleLight',
  'consoleDark',
  'retroTeal',
  'wesAndersonLight',
  'wesAndersonDark',
];

const shared: ThemeOptions = {
  shape: { borderRadius: 10 },
  typography: {
    fontFamily:
      '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    h6: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 500 },
  },
};

export const buildTheme = (id: ThemeId) => {
  const preset = THEME_PRESETS[id];
  return createTheme({
    ...shared,
    palette: preset.palette,
    contextColours: preset.contextColours,
    statusColours: preset.statusColours,
    appBarBackground: preset.appBarBackground,
    bodyBackgroundImage: preset.bodyBackgroundImage,
    components: {
      ...shared.components,
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            // Citations and traces show ids and counts; tabular figures keep
            // columns from shifting as digits change during streaming.
            fontFeatureSettings: '"tnum"',
            ...(preset.bodyBackgroundImage
              ? { backgroundImage: preset.bodyBackgroundImage, backgroundAttachment: 'fixed' }
              : {}),
          },
        },
      },
      MuiButton: { defaultProps: { disableElevation: true } },
      MuiPaper: { defaultProps: { elevation: 0 } },
    },
  });
};

const STORAGE_KEY = 'ragdrive:theme';

/** Reads the persisted choice, falling back to the default on any failure --
 * a private-browsing tab that throws on `localStorage.getItem` should not
 * break the app, only leave the choice unpersisted for that session. */
export const loadStoredThemeId = (): ThemeId => {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && THEME_IDS.includes(stored as ThemeId)) return stored as ThemeId;
  } catch {
    /* storage unavailable -- fall through to the default */
  }
  return DEFAULT_THEME_ID;
};

export const storeThemeId = (id: ThemeId): void => {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* storage unavailable -- the choice simply won't survive a reload */
  }
};

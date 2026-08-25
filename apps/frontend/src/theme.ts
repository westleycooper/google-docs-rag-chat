import { createTheme, type ThemeOptions } from '@mui/material/styles';

/**
 * Palette anchors on the context classes (ADR-0008), so a colour means the same
 * thing in the meter, a citation chip and the trace timeline. A user learns the
 * mapping once.
 */
export const CONTEXT_COLOURS = {
  system: '#6b7280',
  pinned: '#7c3aed',
  history: '#0ea5e9',
  retrieved: '#10b981',
} as const;

export const STATUS_COLOURS = {
  ok: '#10b981',
  degraded: '#f59e0b',
  down: '#ef4444',
  unknown: '#6b7280',
} as const;

const shared: ThemeOptions = {
  shape: { borderRadius: 10 },
  typography: {
    fontFamily:
      '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    h6: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 500 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        // Citations and traces show ids and counts; tabular figures keep
        // columns from shifting as digits change during streaming.
        body: { fontFeatureSettings: '"tnum"' },
      },
    },
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiPaper: { defaultProps: { elevation: 0 } },
  },
};

export const buildTheme = (mode: 'light' | 'dark') =>
  createTheme({
    ...shared,
    palette: {
      mode,
      primary: { main: mode === 'dark' ? '#818cf8' : '#4f46e5' },
      secondary: { main: CONTEXT_COLOURS.retrieved },
      background:
        mode === 'dark'
          ? { default: '#0b0f19', paper: '#121826' }
          : { default: '#f8fafc', paper: '#ffffff' },
    },
  });

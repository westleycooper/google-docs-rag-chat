import { createTheme, type ThemeOptions } from '@mui/material/styles';

/**
 * A Wes Anderson palette: dusty, filmic pastels rather than saturated primaries
 * -- maroon, mustard, powder teal, warm cream. Retro by way of a 1970s travel
 * poster rather than 1980s neon, and the whole app (both light and dark mode,
 * plus the observability app's always-dark scene) draws from these same
 * families so a colour means the same thing everywhere it appears.
 *
 * Deliberately restrained: every tone here is desaturated a little from where
 * an "obvious" pick would land, which is what keeps a palette like this reading
 * as considered rather than as four bright colours picked at random.
 */

/**
 * Palette anchors on the context classes (ADR-0008), so a colour means the same
 * thing in the meter, a citation chip and the trace timeline. A user learns the
 * mapping once. Kept dark/saturated enough for the white chip labels rendered
 * on top of them in ContextPanel and CitationChip.
 */
export const CONTEXT_COLOURS = {
  system: '#6E5F4C', // warm taupe -- background context, meant to recede
  pinned: '#8B3A42', // maroon -- what the user explicitly chose to keep
  history: '#4F7C8A', // dusty teal-blue -- prior turns
  retrieved: '#C08A2E', // ochre/mustard -- what this turn's retrieval found
} as const;

export const STATUS_COLOURS = {
  ok: '#5B7A4A', // muted olive
  degraded: '#C08A2E', // the same ochre as `retrieved`: one "caution" tone
  down: '#A23B3B', // brick red, not fire-engine red
  unknown: '#6E5F4C', // the same taupe as `system`: absence of information
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
      // Dusty teal in both modes -- brightened for dark so it still reads
      // against a near-black maroon rather than a near-black navy.
      primary: { main: mode === 'dark' ? '#6FB3AC' : '#3D7A79' },
      // Tied to CONTEXT_COLOURS.retrieved deliberately: the accent colour used
      // for primary actions is the same one the meter uses for "what retrieval
      // found this turn," so the app's own accent and its busiest UI concept
      // share one colour rather than two competing for attention.
      secondary: { main: CONTEXT_COLOURS.retrieved },
      error: { main: mode === 'dark' ? '#D2603A' : '#A23B3B' }, // coral / brick
      warning: { main: mode === 'dark' ? '#E3B23C' : '#C08A2E' }, // gold / ochre
      success: { main: mode === 'dark' ? '#8FBF7A' : '#5B7A4A' }, // sage / olive
      info: { main: mode === 'dark' ? '#8FC1D1' : '#4F7C8A' }, // powder / teal
      background:
        mode === 'dark'
          ? { default: '#241417', paper: '#331C20' } // velvet maroon, lit interior
          : { default: '#F3E9D8', paper: '#FBF6EC' }, // warm cream, hotel lobby
      text:
        mode === 'dark'
          ? { primary: '#F2E8D5', secondary: '#C9B79E' }
          : { primary: '#3A2E28', secondary: '#6B5D4F' },
    },
  });

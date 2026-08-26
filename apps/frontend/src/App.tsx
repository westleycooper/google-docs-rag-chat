import { useMemo, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import ChatIcon from '@mui/icons-material/Chat';
import PaletteIcon from '@mui/icons-material/Palette';
import SettingsIcon from '@mui/icons-material/Settings';
import {
  AppBar,
  Box,
  CssBaseline,
  Grid,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  ThemeProvider,
  Toolbar,
  Typography,
} from '@mui/material';
import { ChatView } from '@/features/chat/ChatView';
import { ContextPanel } from '@/features/context/ContextPanel';
import { EvalsPanel } from '@/features/config/EvalsPanel';
import { SourcesPanel } from '@/features/config/SourcesPanel';
import { useListModels } from '@/api/generated/models/models';
import { modelSelected } from '@/store/sessionSlice';
import { useAppDispatch, useAppSelector } from '@/store';
import {
  buildTheme,
  loadStoredThemeId,
  storeThemeId,
  THEME_IDS,
  THEME_PRESETS,
  type ThemeId,
} from '@/theme';

export const App = () => {
  // Lazy initial state, so the very first render already reflects a persisted
  // choice rather than flashing the default theme for one frame.
  const [themeId, setThemeId] = useState<ThemeId>(loadStoredThemeId);
  const theme = useMemo(() => buildTheme(themeId), [themeId]);

  const selectThemeId = (id: ThemeId) => {
    setThemeId(id);
    storeThemeId(id);
  };
  // Tabs are routes, not local state. A configuration page nobody can link to
  // is a page nobody can point a colleague at, and a reload should not silently
  // send you back to the chat.
  const location = useLocation();
  const navigate = useNavigate();
  const tab = location.pathname.startsWith('/configuration') ? 1 : 0;

  const dispatch = useAppDispatch();
  const modelId = useAppSelector((s) => s.session.modelId);
  const { data: models = [] } = useListModels();

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <AppBar
          position="static"
          color="default"
          variant="outlined"
          elevation={0}
          sx={
            theme.appBarBackground
              ? { backgroundImage: theme.appBarBackground }
              : undefined
          }
        >
          {/* dense is 48px; 50% taller is 72px, set explicitly rather than
              relying on a variant whose baseline could change under us. */}
          <Toolbar variant="dense" sx={{ gap: 2, minHeight: 72 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              RAGDrive
            </Typography>

            <Tabs
              value={tab}
              onChange={(_, v: number) => navigate(v === 1 ? '/configuration' : '/')}
              sx={{ minHeight: 0 }}
            >
              <Tab
                icon={<ChatIcon fontSize="small" />}
                iconPosition="start"
                label="Chat"
                sx={{ minHeight: 0 }}
              />
              <Tab
                icon={<SettingsIcon fontSize="small" />}
                iconPosition="start"
                label="Configuration"
                sx={{ minHeight: 0 }}
              />
            </Tabs>

            <Box sx={{ flex: 1 }} />

            <TextField
              select
              size="small"
              label="Model"
              value={modelId ?? models[0]?.model_id ?? ''}
              onChange={(e) => dispatch(modelSelected(e.target.value))}
              disabled={models.length === 0}
              // An empty select reads as a broken control. The model list comes
              // from the Models API, so it is empty precisely when no Anthropic
              // credential is configured -- say that instead.
              helperText={models.length === 0 ? 'No ANTHROPIC_API_KEY' : undefined}
              sx={{
                minWidth: 190,
                '& .MuiFormHelperText-root': { mt: 0, fontSize: 10 },
              }}
            >
              {models.map((model) => (
                <MenuItem key={model.model_id} value={model.model_id}>
                  {model.display_name}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              size="small"
              label="Theme"
              value={themeId}
              onChange={(e) => selectThemeId(e.target.value as ThemeId)}
              slotProps={{
                input: {
                  startAdornment: (
                    <PaletteIcon fontSize="small" sx={{ mr: 1, opacity: 0.7 }} />
                  ),
                },
              }}
              sx={{ minWidth: 200 }}
            >
              {THEME_IDS.map((id) => (
                <MenuItem key={id} value={id}>
                  {THEME_PRESETS[id].label}
                </MenuItem>
              ))}
            </TextField>
          </Toolbar>
        </AppBar>

        <Box sx={{ flex: 1, overflow: 'hidden', p: 2 }}>
          <Routes>
            <Route
              path="/"
              element={
                <Grid container spacing={2} sx={{ height: '100%' }}>
                  <Grid size={{ xs: 12, md: 8 }} sx={{ height: '100%' }}>
                    <ChatView />
                  </Grid>
                  <Grid size={{ xs: 12, md: 4 }} sx={{ height: '100%', overflowY: 'auto' }}>
                    <ContextPanel />
                  </Grid>
                </Grid>
              }
            />
            <Route
              path="/configuration"
              element={
                <Box sx={{ height: '100%', overflowY: 'auto', maxWidth: 900 }}>
                  <Stack spacing={4}>
                    <SourcesPanel />
                    <EvalsPanel />
                  </Stack>
                </Box>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Box>
      </Box>
    </ThemeProvider>
  );
};

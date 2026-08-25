/** The chat surface: message list, citations, trace, composer. */

import { useEffect, useRef, useState } from 'react';
import SendIcon from '@mui/icons-material/Send';
import StopIcon from '@mui/icons-material/Stop';
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { CitationChip } from './CitationChip';
import { TraceTimeline } from './TraceTimeline';
import { useChatStream } from '@/hooks/useChatStream';
import { useAppSelector } from '@/store';

export const ChatView = () => {
  const { ask, cancel, awaiting } = useChatStream();
  const { turns, branched } = useAppSelector((s) => s.session);
  const [draft, setDraft] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  const submit = () => {
    const question = draft.trim();
    if (!question || awaiting) return;
    setDraft('');
    void ask(question);
  };

  return (
    <Stack sx={{ height: '100%' }} spacing={1}>
      <Box sx={{ flex: 1, overflowY: 'auto', px: 1 }}>
        {turns.length === 0 && (
          <Stack alignItems="center" sx={{ mt: 8, opacity: 0.7 }} spacing={1}>
            <Typography variant="h6">Ask your documents a question</Typography>
            <Typography variant="body2" color="text.secondary">
              Answers cite the sources they rest on, and show how they were found.
            </Typography>
          </Stack>
        )}

        <Stack spacing={2}>
          {turns.map((turn) => (
            <Paper
              key={turn.id}
              variant="outlined"
              sx={{
                p: 1.5,
                alignSelf: turn.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: turn.role === 'user' ? '75%' : '100%',
                bgcolor:
                  turn.role === 'user' ? 'action.hover' : 'background.paper',
              }}
            >
              {turn.role === 'assistant' && (
                <TraceTimeline
                  trace={turn.trace}
                  streaming={turn.streaming}
                  branched={branched}
                />
              )}

              {turn.citations.length > 0 && (
                <Stack
                  direction="row"
                  spacing={0.5}
                  flexWrap="wrap"
                  useFlexGap
                  sx={{ mb: 1 }}
                >
                  {turn.citations.map((citation, i) => (
                    <CitationChip
                      key={citation.chunk_id}
                      index={i + 1}
                      citation={citation}
                    />
                  ))}
                </Stack>
              )}

              <Typography
                variant="body2"
                sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
              >
                {turn.text}
                {turn.streaming && !turn.text && (
                  <CircularProgress size={12} sx={{ ml: 0.5 }} />
                )}
              </Typography>

              {turn.error && (
                <Alert severity="error" sx={{ mt: 1, py: 0 }}>
                  {turn.error}
                </Alert>
              )}
            </Paper>
          ))}
        </Stack>
        <div ref={bottomRef} />
      </Box>

      <Paper variant="outlined" sx={{ p: 1 }}>
        <Stack direction="row" spacing={1} alignItems="flex-end">
          <TextField
            fullWidth
            multiline
            maxRows={6}
            size="small"
            placeholder="Ask about your documents…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter is a newline. Multi-line questions are
              // common enough here that the reverse would be a nuisance.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            slotProps={{ htmlInput: { 'aria-label': 'Your question' } }}
          />
          {awaiting ? (
            <IconButton onClick={cancel} aria-label="Stop generating" color="error">
              <StopIcon />
            </IconButton>
          ) : (
            <IconButton
              onClick={submit}
              aria-label="Send question"
              color="primary"
              disabled={!draft.trim()}
            >
              <SendIcon />
            </IconButton>
          )}
        </Stack>
      </Paper>
    </Stack>
  );
};

/**
 * Evaluation management (ADR-0010).
 *
 * Two counts get prominence over the averages: cases the retriever missed
 * entirely, and answers judged fluent-but-ungrounded. Each points at a specific
 * remedy — the first at ingestion or chunking, the second at the prompt — where
 * a mean score points at nothing.
 */

import { useState } from 'react';
import AddIcon from '@mui/icons-material/Add';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import {
  useAddCase,
  useCreateDataset,
  useListDatasets,
  useListEvaluationRuns,
  useStartEvaluation,
} from '@/api/generated/evaluation/evaluation';

const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(0)}%`;

const chipSx = { height: 20, fontSize: 11 } as const;

const RunHistory = ({ datasetId }: { datasetId: string }) => {
  const { data: runs = [] } = useListEvaluationRuns(
    datasetId,
    {},
    // Runs take minutes; polling is how progress shows without a second
    // transport existing only for this panel.
    { query: { refetchInterval: 4000 } },
  );
  if (runs.length === 0) return null;

  return (
    <Stack spacing={1} sx={{ mt: 1.5 }}>
      {runs.slice(0, 4).map((run) => (
        <Paper key={run.run_id} variant="outlined" sx={{ p: 1 }}>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap alignItems="center">
            <Chip size="small" label={run.state} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`v${run.dataset_version}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`recall ${percent(run.mean_recall)}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`nDCG ${percent(run.mean_ndcg)}`} sx={chipSx} />
            <Chip size="small" variant="outlined" label={`faithful ${percent(run.mean_faithfulness)}`} sx={chipSx} />
            {run.missed_entirely_count > 0 && (
              <Chip
                size="small"
                color="warning"
                label={`${run.missed_entirely_count} never retrieved`}
                sx={chipSx}
              />
            )}
            {run.hallucination_count > 0 && (
              <Chip
                size="small"
                color="error"
                label={`${run.hallucination_count} ungrounded`}
                sx={chipSx}
              />
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {run.config.embedding_model} · {run.config.chat_model} · rrf k=
            {run.config.rrf_k} · rerank {run.config.rerank_enabled ? 'on' : 'off'}
          </Typography>
        </Paper>
      ))}
    </Stack>
  );
};

export const EvalsPanel = () => {
  const { data: datasets = [], refetch } = useListDatasets();
  const createDataset = useCreateDataset();
  const addCase = useAddCase();
  const startRun = useStartEvaluation();

  const [newName, setNewName] = useState('');
  const [caseFor, setCaseFor] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [expected, setExpected] = useState('');
  const [error, setError] = useState<string | null>(null);

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Evaluation</Typography>
      {error && <Alert severity="error">{error}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1}>
          <TextField
            size="small"
            fullWidth
            label="New dataset name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Button
            startIcon={<AddIcon />}
            disabled={!newName.trim()}
            onClick={async () => {
              await createDataset.mutateAsync({ data: { name: newName } });
              setNewName('');
              await refetch();
            }}
          >
            Create
          </Button>
        </Stack>
      </Paper>

      {datasets.length === 0 && (
        <Alert severity="info">
          No datasets yet. Create one, then add cases — ideally by promoting real
          answers that were wrong, which keeps the set grounded in actual failures.
        </Alert>
      )}

      {datasets.map((dataset) => (
        <Paper key={dataset.dataset_id} variant="outlined" sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Stack>
              <Typography variant="subtitle2">{dataset.name}</Typography>
              <Typography variant="caption" color="text.secondary">
                version {dataset.version} · {dataset.case_count} case
                {dataset.case_count === 1 ? '' : 's'}
              </Typography>
            </Stack>
            <Stack direction="row" spacing={1}>
              <Button size="small" onClick={() => setCaseFor(dataset.dataset_id)}>
                Add case
              </Button>
              <Button
                size="small"
                startIcon={<PlayArrowIcon />}
                variant="contained"
                disabled={dataset.case_count === 0}
                onClick={async () => {
                  setError(null);
                  try {
                    await startRun.mutateAsync({ datasetId: dataset.dataset_id });
                  } catch (e) {
                    setError(e instanceof Error ? e.message : 'run failed to start');
                  }
                }}
              >
                Run
              </Button>
            </Stack>
          </Stack>
          <RunHistory datasetId={dataset.dataset_id} />
        </Paper>
      ))}

      <Dialog open={caseFor !== null} onClose={() => setCaseFor(null)} fullWidth>
        <DialogTitle>Add an evaluation case</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label="Question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              fullWidth
              multiline
            />
            <TextField
              label="Expected answer (optional)"
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
              helperText="Supplying one enables generation scoring. Leave blank to score retrieval only."
              fullWidth
              multiline
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCaseFor(null)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!question.trim()}
            onClick={async () => {
              if (!caseFor) return;
              await addCase.mutateAsync({
                datasetId: caseFor,
                data: {
                  question,
                  expected_answer: expected.trim() || null,
                  expected_chunk_ids: [],
                  tags: [],
                  source_turn_id: null,
                  notes: null,
                },
              });
              setCaseFor(null);
              setQuestion('');
              setExpected('');
              await refetch();
            }}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

/**
 * Drives one chat turn: opens the SSE stream and dispatches each frame.
 *
 * Frames go straight into Redux rather than local state so the context meter,
 * the trace panel and the message list all read one store — which is what makes
 * them consistent mid-stream rather than three components each guessing.
 */

import { useCallback, useRef } from 'react';
import { streamChat } from '@/api/sse';
import {
  answerStarted,
  citationsReceived,
  deltaReceived,
  questionAsked,
  traceReceived,
  turnFailed,
  turnFinished,
} from '@/store/sessionSlice';
import { useAppDispatch, useAppSelector } from '@/store';

export const useChatStream = () => {
  const dispatch = useAppDispatch();
  const { sessionId, modelId, sourceIds, turns, awaiting } = useAppSelector(
    (s) => s.session,
  );
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const ask = useCallback(
    async (question: string) => {
      if (!question.trim() || awaiting) return;

      // Prior turns as (role, text) pairs. Sent so the server can rebuild the
      // prompt; it re-validates and its own record wins (ADR-0007).
      const history = turns
        .filter((t) => !t.error)
        .map((t) => [t.role, t.text] as [string, string]);

      const controller = new AbortController();
      abortRef.current = controller;

      dispatch(questionAsked({ id: crypto.randomUUID(), text: question }));
      dispatch(answerStarted({ id: crypto.randomUUID() }));

      try {
        for await (const frame of streamChat(
          {
            question,
            ...(sessionId ? { sessionId } : {}),
            ...(modelId ? { modelId } : {}),
            ...(sourceIds.length ? { sourceIds } : {}),
            history,
          },
          controller.signal,
        )) {
          switch (frame.type) {
            case 'trace':
              dispatch(traceReceived(frame.event));
              break;
            case 'citations':
              dispatch(citationsReceived(frame.citations));
              break;
            case 'delta':
              dispatch(deltaReceived(frame.text));
              break;
            case 'finished':
              dispatch(
                turnFinished({
                  budget: frame.budget,
                  degraded: frame.degraded,
                  branched: frame.branched,
                }),
              );
              break;
            case 'error':
              dispatch(turnFailed(frame.message));
              break;
          }
        }
      } catch (error) {
        // An abort is the user cancelling, not a failure to report.
        if (error instanceof DOMException && error.name === 'AbortError') return;
        dispatch(
          turnFailed(error instanceof Error ? error.message : 'stream failed'),
        );
      } finally {
        abortRef.current = null;
      }
    },
    [awaiting, dispatch, turns, modelId, sessionId, sourceIds],
  );

  return { ask, cancel, awaiting };
};

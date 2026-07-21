import type { PromoteStatus } from './api';
import type { Review } from './types';

export type DecisionState =
  | 'ready'
  | 'content_blocked'
  | 'checks_running'
  | 'partial'
  | 'operational'
  | 'resuming'
  | 'waiting'
  | 'deployed';

export interface DecisionPresentation {
  state: DecisionState;
  category: 'Conteúdo' | 'Andamento' | 'Operação' | 'Recuperação';
  headline: string;
  productionTruth: string;
  nextAction: string;
  tone: 'ready' | 'blocked' | 'running' | 'operational' | 'success';
}

export function decisionPresentation(review: Review, live: PromoteStatus | null): DecisionPresentation {
  const attempt = live?.deploy.attempt;
  if (attempt?.terminal_state === 'partial_failed') {
    return {
      state: 'partial', category: 'Operação', tone: 'operational',
      headline: 'A publicação parou depois de alterar produção',
      productionTruth: 'Produção mudou parcialmente. Os estágios concluídos estão registrados abaixo.',
      nextAction: 'Nenhuma ação sua. A KIP retomará as mesmas revisões a partir do Preflight.',
    };
  }
  if (attempt?.terminal_state === 'operational_failed') {
    return {
      state: 'operational', category: 'Operação', tone: 'operational',
      headline: 'Não conseguimos iniciar a publicação',
      productionTruth: attempt.mutation_started
        ? 'Não foi possível confirmar se produção mudou. A KIP verificará o estado antes de retomar.'
        : 'Produção não mudou: a falha ocorreu no Preflight, antes da primeira mutação.',
      nextAction: 'Nenhuma ação sua. A KIP corrigirá a operação e repetirá o Preflight.',
    };
  }
  if (attempt?.terminal_state === 'running' && attempt.run_attempt > 1) {
    return {
      state: 'resuming', category: 'Recuperação', tone: 'running',
      headline: 'A KIP está retomando a publicação',
      productionTruth: attempt.mutation_started
        ? 'A recuperação está convergindo produção para o estado aprovado.'
        : 'A nova tentativa recomeçou no Preflight; nenhuma nova mutação foi confirmada ainda.',
      nextAction: 'Aguarde; você pode fechar esta página. Não envie outra solicitação.',
    };
  }
  if (live?.phase === 'deploy_failed') {
    return {
      state: 'operational', category: 'Operação', tone: 'operational',
      headline: 'A publicação encontrou uma falha operacional',
      productionTruth: 'Não foi possível confirmar se produção mudou porque a evidência do Attempt está indisponível.',
      nextAction: 'Nenhuma ação sua. A KIP verificará o run exato antes de qualquer nova tentativa.',
    };
  }
  if (review.gate.conclusion === 'failure') {
    return {
      state: 'content_blocked', category: 'Conteúdo', tone: 'blocked',
      headline: 'Ajustes necessários antes de promover',
      productionTruth: 'Produção não mudou; os itens abaixo precisam ser corrigidos no Dev.',
      nextAction: 'Corrija os bloqueios indicados no Dev e depois solicite uma nova revisão.',
    };
  }
  if (live?.phase === 'deployed') {
    return {
      state: 'deployed', category: 'Andamento', tone: 'success',
      headline: 'Publicação concluída em produção',
      productionTruth: 'O provider confirmou a implantação das revisões aprovadas.',
      nextAction: 'Nenhuma ação necessária. A versão publicada já pode ser utilizada.',
    };
  }
  if (live?.phase === 'awaiting_approval' || live?.phase === 'merged') {
    return {
      state: 'waiting', category: 'Andamento', tone: 'running',
      headline: 'A publicação aguarda a decisão da Plataforma',
      productionTruth: 'Produção ainda não mudou; o gate permanece fechado.',
      nextAction: 'Aguarde a aprovação da Plataforma. Não envie outra solicitação.',
    };
  }
  if (live?.phase === 'checks_running' || live?.phase === 'deploying' || attempt?.terminal_state === 'running') {
    return {
      state: 'checks_running', category: 'Andamento', tone: 'running',
      headline: live?.phase === 'checks_running'
        ? 'As checagens estão em andamento'
        : 'A publicação está em andamento',
      productionTruth: attempt?.mutation_started
        ? 'A execução entrou nos estágios de produção; acompanhe a evidência ao vivo abaixo.'
        : 'Produção ainda não mudou segundo a última evidência disponível.',
      nextAction: 'Aguarde; não envie outra solicitação enquanto este fluxo estiver ativo.',
    };
  }
  return {
    state: 'ready', category: 'Conteúdo', tone: 'ready',
    headline: 'Pronto para solicitar a promoção',
    productionTruth: 'Nenhuma mutação em produção aconteceu nesta etapa.',
    nextAction: live?.external_url || live?.pr_url
      ? 'Acompanhe o Change Request aberto; não crie uma solicitação paralela.'
      : 'Abra o Change Request para iniciar as checagens governadas.',
  };
}

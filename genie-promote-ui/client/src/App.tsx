import { createBrowserRouter, RouterProvider, NavLink, Outlet } from 'react-router';
import { useState, useEffect } from 'react';
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useIsMobile,
} from '@databricks/appkit-ui/react';
import { CheckCircle2, Circle, Clock, Menu, XCircle } from 'lucide-react';

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? 'bg-primary text-primary-foreground'
      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;

const mobileNavLinkClass = ({ isActive }: { isActive: boolean }) =>
  `block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? 'bg-primary text-primary-foreground'
      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
  }`;

type NavLinkClassFn = (props: { isActive: boolean }) => string;

function NavLinks({ className, linkClass, onClick }: { className?: string; linkClass: NavLinkClassFn; onClick?: () => void }) {
  return (
    <nav className={className}>
      <NavLink to="/" end className={linkClass} onClick={onClick}>
        Home
      </NavLink>
    </nav>
  );
}

function Layout() {
  const isMobile = useIsMobile();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Close mobile nav when viewport crosses to desktop (scaffold behavior).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!isMobile) setMobileNavOpen(false);
  }, [isMobile]);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b px-4 md:px-6 py-3 flex items-center gap-4">
        <h1 className="text-lg font-semibold text-foreground">Promotor de Genie/AI-BI</h1>
        {/* Desktop nav — hidden below md breakpoint */}
        <NavLinks className="hidden md:flex gap-1" linkClass={navLinkClass} />
        {/* Mobile nav — visible below md breakpoint */}
        <div className="ml-auto md:hidden">
          <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <Button variant="ghost" size="icon" onClick={() => setMobileNavOpen(true)}>
              <Menu className="h-5 w-5" />
              <span className="sr-only">Open navigation</span>
            </Button>
            <SheetContent side="left">
              <SheetHeader>
                <SheetTitle>Navigation</SheetTitle>
              </SheetHeader>
              <NavLinks className="flex flex-col gap-1" linkClass={mobileNavLinkClass} onClick={() => setMobileNavOpen(false)} />
            </SheetContent>
          </Sheet>
        </div>
      </header>

      <main className="flex-1 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <HomePage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}

interface Space {
  space_id: string;
  title: string;
}

interface Review {
  findings: {
    rule_id: string;
    severity: string;
    message: string;
    citation?: string;
    suggestion?: string;
  }[];
  gate: { conclusion: string; blocker_count: number; summary: string };
  eval: { status: string; summary: string; pass_rate?: number | null; n?: number };
  allowlist_violations: string[];
  consumer_group: string;
  timeline: { key: string; label: string; status: string }[];
}

const TIMELINE_ICON: Record<string, { Icon: typeof CheckCircle2; cls: string }> = {
  pass: { Icon: CheckCircle2, cls: 'text-success' },
  fail: { Icon: XCircle, cls: 'text-destructive' },
  running: { Icon: Clock, cls: 'text-warning' },
  pending: { Icon: Circle, cls: 'text-muted-foreground' },
};

const SEVERITY_VARIANT: Record<string, 'destructive' | 'secondary' | 'outline'> = {
  BLOCKER: 'destructive',
  SUGGESTION: 'secondary',
  STYLE: 'outline',
};

// AK8: separation-of-duties approval (mirrors app_logic.can_approve + the BLOCKER gate).
function ApprovalSection({
  failed,
  approved,
  persona,
  requester,
  approver,
  onApprove,
}: {
  failed: boolean;
  approved: boolean;
  persona: 'author' | 'steward';
  requester: string | null;
  approver: string | null;
  onApprove: () => void;
}) {
  if (persona !== 'steward') {
    return (
      <p className="text-sm text-muted-foreground">
        Aguardando o Steward aprovar. Você é o solicitante — não pode aprovar a própria promoção (SoD).
      </p>
    );
  }
  if (failed) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Promoção bloqueada por achados BLOCKER — resolva (ex.: /genie-fix) antes de aprovar.
        </AlertDescription>
      </Alert>
    );
  }
  if (approved) {
    return (
      <p className="text-sm font-medium text-success">
        ✓ Aprovado pelo Steward — o gate de produção seria liberado; o service principal faz o deploy.
      </p>
    );
  }
  if (!approver || approver === requester) {
    return (
      <p className="text-sm text-muted-foreground">
        Segregação de funções: o solicitante não pode aprovar a própria promoção.
      </p>
    );
  }
  return <Button onClick={onApprove}>✔ Aprovar promoção</Button>;
}

// AK7: the full review result — pipeline timeline + gate + findings cards + AI-trust.
// AK8 adds the Steward approval section + advances the timeline once approved.
function ReviewPanel({
  review,
  userEmail,
  persona,
  steward,
  approved,
  onApprove,
}: {
  review: Review;
  userEmail: string | null;
  persona: 'author' | 'steward';
  steward: string | null;
  approved: boolean;
  onApprove: () => void;
}) {
  const failed = review.gate.conclusion === 'failure';
  const requester = userEmail;
  const approver = persona === 'steward' ? steward : requester;
  // Once the Steward approves, advance the approval + deploy rows (build_timeline isn't over HTTP).
  const timeline = approved
    ? review.timeline.map((t) =>
        t.key === 'approval' || t.key === 'deploy' ? { ...t, status: 'pass' } : t,
      )
    : review.timeline;
  return (
    <div className="space-y-4 border-t pt-4">
      {/* AI-trust: who it ran as + that findings are AI-generated. */}
      <div className="rounded-md border p-3 space-y-1">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="secondary">{userEmail ?? 'usuário autenticado'}</Badge>
          <span className="text-muted-foreground">
            Achados gerados por IA — verifique antes de aprovar.
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Leitura dos espaços executa como você (OBO); o revisor (LLM) e a checagem de grants
          executam como o service principal do app.
        </p>
      </div>

      {/* Pipeline timeline. */}
      <div>
        <h3 className="text-sm font-semibold text-foreground mb-2">Pipeline de promoção</h3>
        <ul className="space-y-1">
          {timeline.map((t) => {
            const { Icon, cls } = TIMELINE_ICON[t.status] ?? TIMELINE_ICON.pending;
            return (
              <li key={t.key} className="flex items-center gap-2 text-sm text-foreground">
                <Icon className={`h-4 w-4 shrink-0 ${cls}`} aria-hidden />
                <span>{t.label}</span>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Gate summary (Alert has no success variant, so render non-failure with a token color). */}
      {failed ? (
        <Alert variant="destructive">
          <AlertDescription>{review.gate.summary}</AlertDescription>
        </Alert>
      ) : (
        <p
          className={`text-sm font-medium ${
            review.gate.conclusion === 'success' ? 'text-success' : 'text-warning'
          }`}
        >
          {review.gate.summary}
        </p>
      )}

      {/* Findings. */}
      <div>
        <h3 className="text-sm font-semibold text-foreground mb-2">Achados do Genie Reviewer</h3>
        {review.findings.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nenhum achado — espaço limpo.</p>
        ) : (
          <div className="space-y-2">
            {review.findings.map((f, i) => (
              // Findings have no id and a (rule_id, message) pair isn't guaranteed unique; the
              // list is static and never reordered, so the index is a safe, unique key here.
              // eslint-disable-next-line react/no-array-index-key
              <Card key={`${f.rule_id}:${i}`}>
                <CardContent className="py-3 space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={SEVERITY_VARIANT[f.severity] ?? 'secondary'}>{f.severity}</Badge>
                    <span className="font-medium text-foreground">{f.rule_id}</span>
                  </div>
                  <p className="text-sm text-foreground">{f.message}</p>
                  {f.suggestion && (
                    <p className="text-sm text-muted-foreground">↳ {f.suggestion}</p>
                  )}
                  {f.citation && (
                    <p className="text-xs italic text-muted-foreground">{f.citation}</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-muted-foreground">eval-run: {review.eval.summary}</p>

      {/* Steward approval (separation of duties). */}
      <div className="border-t pt-3">
        <ApprovalSection
          failed={failed}
          approved={approved}
          persona={persona}
          requester={requester}
          approver={approver}
          onApprove={onApprove}
        />
      </div>
    </div>
  );
}

// AK6: "Meus espaços" — list the user's Genie spaces (OBO via /api/spaces), pick one, and
// request a promotion review (POST /api/review); AK7's ReviewPanel renders the result.
function HomePage() {
  const [spaces, setSpaces] = useState<Space[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [review, setReview] = useState<Review | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [steward, setSteward] = useState<string | null>(null);
  const [persona, setPersona] = useState<'author' | 'steward'>('author');
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    fetch('/api/spaces')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { spaces?: Space[] }) => setSpaces(d.spaces ?? []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    fetch('/api/whoami')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { email?: string | null; steward?: string | null }) => {
        setUserEmail(d.email ?? null);
        setSteward(d.steward ?? null);
      })
      .catch(() => setUserEmail(null));
  }, []);

  const requestPromotion = async () => {
    if (!selected) return;
    setReviewing(true);
    setReview(null);
    setReviewError(null);
    setApproved(false); // a fresh review is not yet approved
    try {
      const r = await fetch('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ space_id: selected }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setReview((await r.json()) as Review);
    } catch (e: unknown) {
      setReviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setReviewing(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto mt-6">
      <Tabs defaultValue="spaces">
        <TabsList>
          <TabsTrigger value="spaces">Meus espaços</TabsTrigger>
          <TabsTrigger value="new">＋ Novo Genie Space</TabsTrigger>
        </TabsList>

        <TabsContent value="spaces">
          <Card>
            <CardHeader>
              <CardTitle>Meus espaços</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Persona toggle (demo): view as the Author (requester) or the Steward (approver). */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Logado como:</span>
                <Select
                  value={persona}
                  onValueChange={(v) => setPersona(v === 'steward' ? 'steward' : 'author')}
                >
                  <SelectTrigger className="w-40" aria-label="Persona">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="author">Autor</SelectItem>
                    <SelectItem value="steward">Steward</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {error && (
                <Alert variant="destructive">
                  <AlertDescription>Não foi possível listar os espaços: {error}</AlertDescription>
                </Alert>
              )}
              {!error && spaces === null && <Skeleton className="h-10 w-full" />}
              {!error && spaces?.length === 0 && (
                <Empty>
                  <EmptyHeader>
                    <EmptyTitle>Nenhum Genie Space encontrado</EmptyTitle>
                    <EmptyDescription>
                      Crie um na aba “＋ Novo Genie Space” para começar a promover.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
              {!error && spaces && spaces.length > 0 && (
                <div className="space-y-3">
                  <Select
                    value={selected}
                    onValueChange={(v) => {
                      setSelected(v);
                      setReview(null); // clear a prior space's verdict so it can't mislead
                      setReviewError(null);
                    }}
                  >
                    <SelectTrigger aria-label="Espaço">
                      <SelectValue placeholder="Selecione um espaço" />
                    </SelectTrigger>
                    <SelectContent>
                      {spaces.map((s) => (
                        <SelectItem key={s.space_id} value={s.space_id}>
                          {s.title}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button onClick={() => void requestPromotion()} disabled={!selected || reviewing}>
                    {reviewing ? 'Revisando…' : 'Solicitar promoção →'}
                  </Button>
                </div>
              )}

              {reviewError && (
                <Alert variant="destructive">
                  <AlertDescription>Não foi possível revisar o espaço: {reviewError}</AlertDescription>
                </Alert>
              )}
              {review && (
                <ReviewPanel
                  review={review}
                  userEmail={userEmail}
                  persona={persona}
                  steward={steward}
                  approved={approved}
                  onApprove={() => setApproved(true)}
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="new">
          <Card>
            <CardHeader>
              <CardTitle>Novo Genie Space</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Em breve — assistente de criação (autoria rica acontece no Genie nativo).
              </p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

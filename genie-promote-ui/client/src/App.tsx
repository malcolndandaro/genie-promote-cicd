import { createBrowserRouter, RouterProvider, NavLink, Outlet } from 'react-router';
import { useState, useEffect } from 'react';
import {
  Alert,
  AlertDescription,
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
import { Menu } from 'lucide-react';

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

// AK6: "Meus espaços" — list the user's Genie spaces (OBO via /api/spaces), pick one, and
// request a promotion review (POST /api/review). The review result is shown minimally here;
// AK7 builds the full timeline + findings + AI-trust panel.
function HomePage() {
  const [spaces, setSpaces] = useState<Space[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [review, setReview] = useState<Review | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/spaces')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { spaces?: Space[] }) => setSpaces(d.spaces ?? []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const requestPromotion = async () => {
    if (!selected) return;
    setReviewing(true);
    setReview(null);
    setReviewError(null);
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
                    <SelectTrigger>
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

              {/* Minimal result — AK7 replaces this with the full timeline + findings + AI-trust. */}
              {reviewError && (
                <Alert variant="destructive">
                  <AlertDescription>Não foi possível revisar o espaço: {reviewError}</AlertDescription>
                </Alert>
              )}
              {review && (
                <Alert variant={review.gate.conclusion === 'failure' ? 'destructive' : 'default'}>
                  <AlertDescription>{review.gate.summary}</AlertDescription>
                </Alert>
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

import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Database, Loader2, AlertCircle } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { authApi } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';

export default function ClientLogin() {
  const navigate = useNavigate();
  const { loginClient } = useAuth();

  // Login state
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  // Register state
  const [regEmail, setRegEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regCompany, setRegCompany] = useState('');
  const [regContact, setRegContact] = useState('');
  const [regPhone, setRegPhone] = useState('');
  const [regLoading, setRegLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError('');
    setLoginLoading(true);
    const result = await loginClient(loginEmail, loginPassword);
    if (result.success) {
      navigate('/client/tenders');
    } else {
      setLoginError(result.error || 'Identifiants invalides');
    }
    setLoginLoading(false);
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegLoading(true);
    const result = await authApi.clientRegister({
      email: regEmail,
      password: regPassword,
      company_name: regCompany || undefined,
      contact_name: regContact || undefined,
      phone: regPhone || undefined,
    });
    if (result.success) {
      toast.success('Inscription réussie ! Votre compte est en attente d\'approbation.');
      setRegEmail(''); setRegPassword(''); setRegCompany(''); setRegContact(''); setRegPhone('');
    } else {
      toast.error(result.error || 'Erreur d\'inscription');
    }
    setRegLoading(false);
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="flex items-center justify-center w-12 h-12 rounded-lg bg-primary/10 mx-auto">
            <Database className="w-6 h-6 text-primary" />
          </div>
          <h1 className="text-xl font-semibold">Portail Client</h1>
          <p className="text-sm text-muted-foreground">Accédez aux appels d'offres analysés</p>
        </div>

        <Tabs defaultValue="login" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="login">Connexion</TabsTrigger>
            <TabsTrigger value="register">Inscription</TabsTrigger>
          </TabsList>

          <TabsContent value="login">
            <form onSubmit={handleLogin} className="data-card space-y-4">
              {loginError && (
                <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 border border-destructive/20 text-destructive text-sm">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  {loginError}
                </div>
              )}
              <div className="space-y-2">
                <Label>Email</Label>
                <Input value={loginEmail} onChange={e => setLoginEmail(e.target.value)} type="email" placeholder="vous@entreprise.ma" required autoFocus />
              </div>
              <div className="space-y-2">
                <Label>Mot de passe</Label>
                <Input value={loginPassword} onChange={e => setLoginPassword(e.target.value)} type="password" placeholder="••••••••" required />
              </div>
              <Button type="submit" className="w-full" disabled={loginLoading}>
                {loginLoading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                Se connecter
              </Button>
            </form>
          </TabsContent>

          <TabsContent value="register">
            <form onSubmit={handleRegister} className="data-card space-y-4">
              <div className="space-y-2">
                <Label>Email *</Label>
                <Input value={regEmail} onChange={e => setRegEmail(e.target.value)} type="email" required />
              </div>
              <div className="space-y-2">
                <Label>Mot de passe *</Label>
                <Input value={regPassword} onChange={e => setRegPassword(e.target.value)} type="password" required />
              </div>
              <div className="space-y-2">
                <Label>Nom de l'entreprise</Label>
                <Input value={regCompany} onChange={e => setRegCompany(e.target.value)} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label>Nom du contact</Label>
                  <Input value={regContact} onChange={e => setRegContact(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Téléphone</Label>
                  <Input value={regPhone} onChange={e => setRegPhone(e.target.value)} />
                </div>
              </div>
              <Button type="submit" className="w-full" disabled={regLoading}>
                {regLoading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                S'inscrire
              </Button>
              <p className="text-xs text-muted-foreground text-center">
                Votre compte sera activé après validation par l'administrateur.
              </p>
            </form>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

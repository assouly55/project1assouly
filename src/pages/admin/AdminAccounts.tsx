import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { useAuth } from '@/contexts/AuthContext';
import { authApi, type ClientAccount } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Users, Plus, CheckCircle2, XCircle, Trash2, Loader2, UserCheck, UserX, Search } from 'lucide-react';
import { toast } from 'sonner';

export default function AdminAccounts() {
  const { token } = useAuth();
  const [clients, setClients] = useState<ClientAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  // Create form state
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newCompany, setNewCompany] = useState('');
  const [newContact, setNewContact] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [creating, setCreating] = useState(false);

  const fetchClients = async () => {
    if (!token) return;
    setLoading(true);
    const result = await authApi.listClients(token);
    if (result.success && result.data) setClients(result.data);
    setLoading(false);
  };

  useEffect(() => { fetchClients(); }, [token]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setCreating(true);
    const result = await authApi.createClient(token, {
      email: newEmail,
      password: newPassword,
      company_name: newCompany || undefined,
      contact_name: newContact || undefined,
      phone: newPhone || undefined,
    });
    if (result.success) {
      toast.success('Client créé avec succès');
      setShowCreateDialog(false);
      setNewEmail(''); setNewPassword(''); setNewCompany(''); setNewContact(''); setNewPhone('');
      fetchClients();
    } else {
      toast.error(result.error || 'Erreur');
    }
    setCreating(false);
  };

  const handleApprove = async (id: string) => {
    if (!token) return;
    const result = await authApi.approveClient(token, id);
    if (result.success) { toast.success('Client approuvé'); fetchClients(); }
    else toast.error(result.error || 'Erreur');
  };

  const handleSuspend = async (id: string) => {
    if (!token) return;
    const result = await authApi.suspendClient(token, id);
    if (result.success) { toast.success('Statut mis à jour'); fetchClients(); }
    else toast.error(result.error || 'Erreur');
  };

  const handleDelete = async (id: string) => {
    if (!token || !confirm('Supprimer ce client ?')) return;
    const result = await authApi.deleteClient(token, id);
    if (result.success) { toast.success('Client supprimé'); fetchClients(); }
    else toast.error(result.error || 'Erreur');
  };

  const filtered = clients.filter(c =>
    c.email.toLowerCase().includes(searchQuery.toLowerCase()) ||
    (c.company_name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
    (c.contact_name || '').toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Gestion des Comptes</h1>
            <p className="text-muted-foreground text-sm mt-1">{clients.length} client(s) enregistré(s)</p>
          </div>

          <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
            <DialogTrigger asChild>
              <Button><Plus className="w-4 h-4 mr-2" />Créer un Client</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Nouveau Compte Client</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4">
                <div className="space-y-2">
                  <Label>Email *</Label>
                  <Input value={newEmail} onChange={e => setNewEmail(e.target.value)} type="email" required />
                </div>
                <div className="space-y-2">
                  <Label>Mot de passe *</Label>
                  <Input value={newPassword} onChange={e => setNewPassword(e.target.value)} type="password" required />
                </div>
                <div className="space-y-2">
                  <Label>Entreprise</Label>
                  <Input value={newCompany} onChange={e => setNewCompany(e.target.value)} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label>Contact</Label>
                    <Input value={newContact} onChange={e => setNewContact(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label>Téléphone</Label>
                    <Input value={newPhone} onChange={e => setNewPhone(e.target.value)} />
                  </div>
                </div>
                <Button type="submit" className="w-full" disabled={creating}>
                  {creating ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                  Créer le Compte
                </Button>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        {/* Search */}
        <div className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Rechercher..."
            className="pl-10"
          />
        </div>

        {/* Table */}
        <div className="data-card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Email</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Entreprise</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Contact</th>
                <th className="text-center py-3 px-4 font-medium text-muted-foreground">Statut</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Créé le</th>
                <th className="text-right py-3 px-4 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="py-12 text-center"><Loader2 className="w-6 h-6 animate-spin text-primary mx-auto" /></td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={6} className="py-12 text-center text-muted-foreground">Aucun client trouvé</td></tr>
              ) : filtered.map(client => (
                <tr key={client.id} className="border-b border-border/50 hover:bg-muted/20">
                  <td className="py-3 px-4 font-mono text-xs">{client.email}</td>
                  <td className="py-3 px-4">{client.company_name || '—'}</td>
                  <td className="py-3 px-4">{client.contact_name || '—'}</td>
                  <td className="py-3 px-4 text-center">
                    <div className="flex items-center justify-center gap-2">
                      {!client.is_approved ? (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-warning/15 text-warning">En attente</span>
                      ) : !client.is_active ? (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-destructive/15 text-destructive">Suspendu</span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-success/15 text-success">Actif</span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-4 text-xs text-muted-foreground">
                    {new Date(client.created_at).toLocaleDateString('fr-FR')}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center justify-end gap-1">
                      {!client.is_approved && (
                        <Button size="sm" variant="ghost" onClick={() => handleApprove(client.id)} title="Approuver">
                          <UserCheck className="w-4 h-4 text-success" />
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => handleSuspend(client.id)} title={client.is_active ? 'Suspendre' : 'Réactiver'}>
                        {client.is_active ? <UserX className="w-4 h-4 text-warning" /> : <CheckCircle2 className="w-4 h-4 text-success" />}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleDelete(client.id)} title="Supprimer">
                        <Trash2 className="w-4 h-4 text-destructive" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </AdminLayout>
  );
}

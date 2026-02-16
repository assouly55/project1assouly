import { useState } from 'react';
import { ClientLayout } from '@/components/layout/ClientLayout';
import { useAuth } from '@/contexts/AuthContext';
import { authApi } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { User, Building2, Mail, Phone, Save, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

export default function ClientProfile() {
  const { user, token } = useAuth();
  const [email, setEmail] = useState(user?.email || '');
  const [companyName, setCompanyName] = useState(user?.company_name || '');
  const [contactName, setContactName] = useState(user?.contact_name || '');
  const [phone, setPhone] = useState(user?.phone || '');
  const [saving, setSaving] = useState(false);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    const result = await authApi.clientUpdateProfile(token, {
      email,
      company_name: companyName,
      contact_name: contactName,
      phone,
    });
    if (result.success) {
      toast.success('Profil mis à jour');
    } else {
      toast.error(result.error || 'Erreur');
    }
    setSaving(false);
  };

  return (
    <ClientLayout>
      <div className="max-w-xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Mon Profil</h1>
          <p className="text-muted-foreground text-sm mt-1">Gérez vos informations</p>
        </div>

        <form onSubmit={handleSave} className="data-card space-y-5">
          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Mail className="w-4 h-4 text-muted-foreground" />
              Email
            </Label>
            <Input value={email} onChange={e => setEmail(e.target.value)} type="email" />
          </div>

          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Building2 className="w-4 h-4 text-muted-foreground" />
              Nom de l'entreprise
            </Label>
            <Input value={companyName} onChange={e => setCompanyName(e.target.value)} />
          </div>

          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <User className="w-4 h-4 text-muted-foreground" />
              Nom du contact
            </Label>
            <Input value={contactName} onChange={e => setContactName(e.target.value)} />
          </div>

          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Phone className="w-4 h-4 text-muted-foreground" />
              Téléphone
            </Label>
            <Input value={phone} onChange={e => setPhone(e.target.value)} />
          </div>

          <Button type="submit" disabled={saving}>
            {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
            Enregistrer
          </Button>
        </form>
      </div>
    </ClientLayout>
  );
}

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Info, Copy, RefreshCw, Eye, EyeOff, Check } from "lucide-react";
import { UserSettings, Organization } from "@/api/entities";

const PrinterConnectionSettings = ({ userSettings, onUpdate }) => {
  const [recipientId, setRecipientId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [copiedApiKey, setCopiedApiKey] = useState(false);
  const [copiedRecipient, setCopiedRecipient] = useState(false);
  const [organization, setOrganization] = useState(null);

  useEffect(() => {
    if (userSettings) {
      setRecipientId(userSettings.defaultRecipientId || "");
    }
  }, [userSettings]);

  useEffect(() => {
    loadOrganization();
  }, []);

  const loadOrganization = async () => {
    try {
      const { User } = await import("@/api/entities");
      const user = await User.me();

      if (user.organizationId) {
        const org = await Organization.findById(user.organizationId);
        setOrganization(org);
        setApiKey(org.api_key || '');
      }
    } catch (error) {
      console.error('Error loading organization:', error);
    }
  };

  const handleSave = async () => {
    if (!recipientId.trim()) {
      alert('Recipient ID kan ikke være tom');
      return;
    }

    setIsSaving(true);

    try {
      const { User } = await import("@/api/entities");
      const user = await User.me();

      if (!user.organizationId) {
        alert('No organization found. Please log in again.');
        setIsSaving(false);
        return;
      }

      if (userSettings) {
        await UserSettings.update(userSettings.id, {
          ...userSettings,
          defaultRecipientId: recipientId.trim()
        });
      } else {
        await UserSettings.create({
          userId: user.id,
          organizationId: user.organizationId,
          companyName: user.full_name || 'My Company',
          defaultRecipientId: recipientId.trim()
        });
      }
      onUpdate();
    } catch (error) {
      console.error('Error saving settings:', error);
      alert('Kunne ikke lagre innstillinger');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRegenerateApiKey = async () => {
    if (!confirm('Er du sikker på at du vil regenerere API-nøkkelen? Den gamle nøkkelen vil slutte å fungere.')) {
      return;
    }

    setIsRegenerating(true);
    try {
      const { User } = await import("@/api/entities");
      const user = await User.me();

      if (!user.organizationId) {
        alert('Ingen organisasjon funnet');
        return;
      }

      const response = await fetch(
        `/api/organizations/${user.organizationId}/regenerate-api-key`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('auth_token')}`,
            'Content-Type': 'application/json'
          }
        }
      );

      if (!response.ok) throw new Error('Kunne ikke regenerere API-nøkkel');

      const data = await response.json();
      setApiKey(data.data.api_key);
      alert('API-nøkkel regenerert! Husk å oppdatere printer-klienten.');
    } catch (error) {
      console.error('Error regenerating API key:', error);
      alert('Kunne ikke regenerere API-nøkkel');
    } finally {
      setIsRegenerating(false);
    }
  };

  const currentPathSegments = window.location.pathname.split('/');
  const appId = currentPathSegments[2];
  const statusEndpoint = `${window.location.origin}/api/apps/${appId}/functions/updatePrinterStatus`;

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-2">Printer Backend Connection</h3>
        <p className="text-sm text-slate-500 mb-4">
          Configure authentication for your printer clients
        </p>
        <Alert className="bg-yellow-50 border-yellow-200 mb-4">
          <Info className="h-4 w-4 text-yellow-600" />
          <AlertDescription className="text-yellow-800 text-sm">
            <strong>Viktig:</strong> API-nøkkelen gir full tilgang til din organisasjons printere. Del den aldri offentlig!
          </AlertDescription>
        </Alert>
      </div>

      <Alert className="bg-blue-50 border-blue-200">
        <Info className="h-4 w-4 text-blue-600" />
        <AlertDescription className="text-blue-800">
          <div className="space-y-2">
            <div>
              <strong>Job Upload URL:</strong>
              <code className="block mt-1 p-2 bg-white rounded text-xs">https://printpro3d-api-931368217793.europe-west1.run.app/upload</code>
            </div>
            <div>
              <strong>Status Updates URL:</strong>
              <code className="block mt-1 p-2 bg-white rounded text-xs break-all">{statusEndpoint}</code>
            </div>
            <p className="text-xs mt-2">
              Printer agents should send status updates with the API key for authentication.
            </p>
          </div>
        </AlertDescription>
      </Alert>

      <div className="space-y-4">
        {/* API Key Section */}
        <Card className="border-2 border-blue-100">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              🔑 API Key
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label htmlFor="api-key">Organization API Key</Label>
              <div className="flex gap-2 mt-1">
                <div className="relative flex-1">
                  <Input
                    id="api-key"
                    type={showApiKey ? "text" : "password"}
                    value={apiKey}
                    readOnly
                    className="font-mono pr-10"
                  />
                  <button
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                  >
                    {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    navigator.clipboard.writeText(apiKey);
                    setCopiedApiKey(true);
                    setTimeout(() => setCopiedApiKey(false), 2000);
                  }}
                >
                  {copiedApiKey ? <Check size={16} /> : <Copy size={16} />}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRegenerateApiKey}
                  disabled={isRegenerating}
                >
                  <RefreshCw size={16} className={isRegenerating ? "animate-spin" : ""} />
                </Button>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                Bruk denne nøkkelen i printer-klienten for autentisering
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Recipient ID Section */}
        <div>
          <Label htmlFor="recipient-id">Recipient ID</Label>
          <div className="flex gap-2 mt-1">
            <Input
              id="recipient-id"
              value={recipientId}
              onChange={(e) => setRecipientId(e.target.value)}
              placeholder="user-123"
              className="font-mono"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                navigator.clipboard.writeText(recipientId);
                setCopiedRecipient(true);
                setTimeout(() => setCopiedRecipient(false), 2000);
              }}
            >
              {copiedRecipient ? <Check size={16} /> : <Copy size={16} />}
            </Button>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Din unike kanal-ID for printer-agenten
          </p>
        </div>

        <Button onClick={handleSave} disabled={isSaving}>
          {isSaving ? 'Lagrer...' : 'Lagre Recipient ID'}
        </Button>
      </div>

      {/* Usage Example */}
      <Card className="bg-slate-50">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Eksempel: Printer Client Konfigurasjon</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="text-xs bg-slate-900 text-slate-100 p-3 rounded overflow-x-auto">
{`# Legg til i .env filen til printer-klienten
PRINTER_BACKEND_API_KEY=${apiKey || 'pk_XXXXXXXXXXXXXX'}
PRINTER_BACKEND_BASE_URL=https://printpro3d-api-931368217793.europe-west1.run.app
BASE44_RECIPIENT_ID=${recipientId || 'user-123'}

# Eller bruk som kommandolinje-argumenter:
python -m client.client listen \\
  --apiKey ${apiKey || 'pk_XXXXXXXXXXXXXX'} \\
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \\
  --recipientId ${recipientId || 'user-123'}`}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
};

export default PrinterConnectionSettings;

import { useState } from 'react'
import { Eye, EyeOff, ExternalLink, X } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import type { ConfigSectionProps } from './types'

export function ExternalServices({ config, onChange }: ConfigSectionProps) {
  const [showMediaFlowPassword, setShowMediaFlowPassword] = useState(false)
  const [showRpdbKey, setShowRpdbKey] = useState(false)
  const [showMdblistKey, setShowMdblistKey] = useState(false)

  const mfc = config.mfc
  const rpc = config.rpc
  const mdb = config.mdb

  const enableMediaFlow = (enabled: boolean) => {
    if (enabled) {
      onChange({ ...config, mfc: { pu: '', ap: '', pls: false, ewp: false } })
    } else {
      onChange({ ...config, mfc: undefined })
    }
  }

  const updateMediaFlow = (updates: Record<string, unknown>) => {
    onChange({ ...config, mfc: { ...mfc, ...updates } })
  }

  const enableRpdb = (enabled: boolean) => {
    if (enabled) {
      onChange({ ...config, rpc: { ak: '' } })
    } else {
      onChange({ ...config, rpc: undefined })
    }
  }

  const enableMdblist = (enabled: boolean) => {
    if (enabled) {
      onChange({ ...config, mdb: { ak: '', l: [] } })
    } else {
      onChange({ ...config, mdb: undefined })
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">🔌 External Services</CardTitle>
        <CardDescription>Configure additional services to enhance your streaming experience</CardDescription>
      </CardHeader>
      <CardContent>
        <Accordion type="multiple" className="w-full">
          {/* MediaFlow Proxy */}
          <AccordionItem value="mediaflow">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>MediaFlow Proxy</span>
                {mfc && (
                  <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-500">
                    Enabled
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Enable MediaFlow</Label>
                  <p className="text-xs text-muted-foreground">
                    Use MediaFlow for handling various stream types and routing
                  </p>
                </div>
                <Switch checked={!!mfc} onCheckedChange={enableMediaFlow} />
              </div>

              {mfc && (
                <>
                  <div className="flex items-center gap-2 text-sm">
                    <a
                      href="https://github.com/mhdzumair/mediaflow-proxy"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline flex items-center gap-1"
                    >
                      MediaFlow Setup Guide <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>

                  <div className="space-y-2">
                    <Label>Proxy URL</Label>
                    <Input
                      value={mfc.pu || ''}
                      onChange={(e) => updateMediaFlow({ pu: e.target.value })}
                      placeholder="https://your-mediaflow-proxy.com"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label>API Password</Label>
                    <div className="relative">
                      <Input
                        type={showMediaFlowPassword ? 'text' : 'password'}
                        value={mfc.ap || ''}
                        onChange={(e) => updateMediaFlow({ ap: e.target.value })}
                        placeholder="Enter API password"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 top-0 h-full px-3"
                        onClick={() => setShowMediaFlowPassword(!showMediaFlowPassword)}
                      >
                        {showMediaFlowPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>Public IP (Optional)</Label>
                    <Input
                      value={mfc.pip || ''}
                      onChange={(e) => updateMediaFlow({ pip: e.target.value })}
                      placeholder="Only for local MediaFlow with proxy"
                    />
                    <p className="text-xs text-muted-foreground">
                      Only configure when running MediaFlow locally with a proxy service
                    </p>
                  </div>

                  <div className="space-y-4 pt-2">
                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <Label>Proxy Live Streams</Label>
                        <p className="text-xs text-muted-foreground">Route IPTV/live TV streams through MediaFlow</p>
                      </div>
                      <Switch
                        checked={mfc.pls === true}
                        onCheckedChange={(checked) => updateMediaFlow({ pls: checked })}
                      />
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <Label>Enable Web Browser Playback</Label>
                        <p className="text-xs text-muted-foreground">
                          Required to play debrid streams in MediaFusion web UI
                        </p>
                      </div>
                      <Switch
                        checked={mfc.ewp === true}
                        onCheckedChange={(checked) => updateMediaFlow({ ewp: checked })}
                      />
                    </div>

                    <div className="p-3 rounded-lg bg-muted/50 text-sm space-y-2">
                      <p className="font-medium">Stremio/Kodi MediaFlow Proxy</p>
                      <p className="text-xs text-muted-foreground">
                        To enable/disable MediaFlow proxy for Stremio or Kodi, go to your streaming provider settings
                        and toggle "Use MediaFlow Proxy" for each provider individually.
                      </p>
                    </div>
                  </div>
                </>
              )}
            </AccordionContent>
          </AccordionItem>

          {/* RPDB */}
          <AccordionItem value="rpdb">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>RPDB (Rating Poster DB)</span>
                {rpc && (
                  <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-500">
                    Enabled
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Enable RPDB Posters</Label>
                  <p className="text-xs text-muted-foreground">Use RPDB for enhanced rating posters</p>
                </div>
                <Switch checked={!!rpc} onCheckedChange={enableRpdb} />
              </div>

              {rpc && (
                <>
                  <div className="flex items-center gap-2 text-sm">
                    <a
                      href="https://ratingposterdb.com/api-key/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline flex items-center gap-1"
                    >
                      Get RPDB API Key <ExternalLink className="h-3 w-3" />
                    </a>
                    <span className="text-muted-foreground">|</span>
                    <a
                      href="https://manager.ratingposterdb.com"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline flex items-center gap-1"
                    >
                      Configure Posters <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>

                  <p className="text-xs text-muted-foreground">
                    RPDB is an optional freemium service for enhanced rating posters. MediaFusion generates rating
                    posters using IMDb ratings by default.
                  </p>

                  <div className="space-y-2">
                    <Label>API Key</Label>
                    <div className="relative">
                      <Input
                        type={showRpdbKey ? 'text' : 'password'}
                        value={rpc.ak || ''}
                        onChange={(e) => onChange({ ...config, rpc: { ak: e.target.value } })}
                        placeholder="Enter RPDB API key"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 top-0 h-full px-3"
                        onClick={() => setShowRpdbKey(!showRpdbKey)}
                      >
                        {showRpdbKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </AccordionContent>
          </AccordionItem>

          {/* MDBList */}
          <AccordionItem value="mdblist">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>MDBList Integration</span>
                {mdb && (
                  <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-500">
                    Enabled
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Enable MDBList</Label>
                  <p className="text-xs text-muted-foreground">Use MDBList for custom movie and TV show lists</p>
                </div>
                <Switch checked={!!mdb} onCheckedChange={enableMdblist} />
              </div>

              {mdb && (
                <>
                  <div className="flex items-center gap-2 text-sm">
                    <a
                      href="https://mdblist.com/preferences/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline flex items-center gap-1"
                    >
                      Get MDBList API Key <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>

                  <div className="space-y-2">
                    <Label>API Key</Label>
                    <div className="relative">
                      <Input
                        type={showMdblistKey ? 'text' : 'password'}
                        value={mdb.ak || ''}
                        onChange={(e) => onChange({ ...config, mdb: { ...mdb, ak: e.target.value } })}
                        placeholder="Enter MDBList API key"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 top-0 h-full px-3"
                        onClick={() => setShowMdblistKey(!showMdblistKey)}
                      >
                        {showMdblistKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>

                  {/* MDBList Lists would be configured here */}
                  {mdb.ak && mdb.ak !== '••••••••' && (
                    <div className="space-y-2 pt-2">
                      <Label>Configured Lists</Label>
                      {mdb.l && mdb.l.length > 0 ? (
                        <div className="space-y-2">
                          {mdb.l.map((list, index) => (
                            <div key={index} className="flex items-center justify-between p-2 bg-muted rounded-lg">
                              <div>
                                <p className="text-sm font-medium">{list.t}</p>
                                <p className="text-xs text-muted-foreground">{list.ct}</p>
                              </div>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => {
                                  const newLists = mdb.l?.filter((_, i) => i !== index) || []
                                  onChange({ ...config, mdb: { ...mdb, l: newLists } })
                                }}
                              >
                                <X className="h-4 w-4" />
                              </Button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          No lists configured. Verify your API key to manage lists.
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  )
}

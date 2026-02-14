import { useState, useEffect } from 'react'
import {
  Plus,
  Trash2,
  Eye,
  EyeOff,
  ExternalLink,
  AlertCircle,
  List,
  Film,
  Tv,
  ChevronUp,
  ChevronDown,
  Search,
  Loader2,
  Check,
  Trophy,
  User,
  PlusCircle,
  Edit2,
  Heart,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Checkbox } from '@/components/ui/checkbox'
import type { ConfigSectionProps, MDBListConfig, MDBListItem, CatalogConfig } from './types'

const MDBLIST_API_BASE = 'https://api.mdblist.com'

const MDBLIST_SORT_OPTIONS = [
  { value: 'rank', label: 'Rank' },
  { value: 'score', label: 'Score' },
  { value: 'released', label: 'Released' },
  { value: 'added', label: 'Added' },
  { value: 'title', label: 'Title' },
  { value: 'runtime', label: 'Runtime' },
  { value: 'budget', label: 'Budget' },
  { value: 'revenue', label: 'Revenue' },
]

interface MDBListApiList {
  id: number
  name: string
  slug?: string
  mediatype: 'movie' | 'show' | 'all'
  items: number
  likes: number
  user_name?: string
}

// MDBList API helper
const mdblistApi = {
  async makeRequest(endpoint: string, apiKey: string) {
    const url = new URL(endpoint.startsWith('http') ? endpoint : `${MDBLIST_API_BASE}${endpoint}`)
    url.searchParams.append('apikey', apiKey)

    const response = await fetch(url.toString())

    if (response.status === 403) throw new Error('Invalid API key')
    if (response.status === 404) throw new Error('Resource not found')
    if (response.status === 429) throw new Error('Rate limit exceeded')
    if (!response.ok) throw new Error(`API request failed with status ${response.status}`)

    return response.json()
  },

  async verifyApiKey(apiKey: string): Promise<boolean> {
    if (!apiKey?.trim()) return false
    try {
      await this.makeRequest('/user', apiKey)
      return true
    } catch {
      return false
    }
  },

  async getUserLists(apiKey: string): Promise<MDBListApiList[]> {
    return this.makeRequest('/lists/user', apiKey)
  },

  async getTopLists(apiKey: string): Promise<MDBListApiList[]> {
    return this.makeRequest('/lists/top', apiKey)
  },

  async searchLists(apiKey: string, query: string): Promise<MDBListApiList[]> {
    if (!query?.trim()) return []
    return this.makeRequest(`/lists/search?query=${encodeURIComponent(query.trim())}`, apiKey)
  },

  async getListDetails(apiKey: string, listId: string): Promise<MDBListApiList> {
    return this.makeRequest(`/lists/${listId}`, apiKey)
  },
}

interface ListItemCardProps {
  list: MDBListApiList
  isAdded: boolean
  onAdd: () => void
}

function ListItemCard({ list, isAdded, onAdd }: ListItemCardProps) {
  return (
    <div className="flex items-center gap-3 p-3 border rounded-lg hover:bg-muted/50 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-medium truncate">{list.name}</p>
          {list.user_name && <span className="text-xs text-muted-foreground">by {list.user_name}</span>}
        </div>
        <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            {list.mediatype === 'movie' ? (
              <Film className="h-3 w-3" />
            ) : list.mediatype === 'show' ? (
              <Tv className="h-3 w-3" />
            ) : (
              <List className="h-3 w-3" />
            )}
            {list.mediatype === 'movie' ? 'Movies' : list.mediatype === 'show' ? 'Series' : 'Mixed'}
          </span>
          <span>{list.items || 0} items</span>
          <span className="flex items-center gap-1">
            <Heart className="h-3 w-3" />
            {list.likes || 0}
          </span>
        </div>
      </div>

      <Button
        variant={isAdded ? 'secondary' : 'outline'}
        size="sm"
        onClick={onAdd}
        disabled={isAdded}
        className="shrink-0"
      >
        {isAdded ? (
          <>
            <Check className="h-4 w-4 mr-1" />
            Added
          </>
        ) : (
          <>
            <Plus className="h-4 w-4 mr-1" />
            Add
          </>
        )}
      </Button>
    </div>
  )
}

interface EditListDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  list: MDBListApiList | null
  existingConfig?: MDBListItem
  onSave: (list: MDBListItem) => void
}

function EditListDialog({ open, onOpenChange, list, existingConfig, onSave }: EditListDialogProps) {
  const [title, setTitle] = useState('')
  const [catalogType, setCatalogType] = useState<'movie' | 'series'>('movie')
  const [useFilters, setUseFilters] = useState(false)
  const [sort, setSort] = useState('rank')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')

  useEffect(() => {
    if (list) {
      setTitle(existingConfig?.t || list.name)
      setCatalogType(existingConfig?.ct || (list.mediatype === 'show' ? 'series' : 'movie'))
      setUseFilters(existingConfig?.uf || false)
      setSort(existingConfig?.s || 'rank')
      setOrder(existingConfig?.o || 'desc')
    }
  }, [list, existingConfig])

  const handleSave = () => {
    if (!list) return

    onSave({
      i: list.id,
      t: title,
      ct: catalogType,
      uf: useFilters,
      s: sort,
      o: order,
    })
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Configure List</DialogTitle>
          <DialogDescription>Customize how this list appears in your catalogs</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>Display Name</Label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="List name"
              autoComplete="off"
              data-form-type="other"
              data-lpignore="true"
              name="mdblist-display-name"
            />
          </div>

          <div className="space-y-2">
            <Label>Catalog Type</Label>
            <Select value={catalogType} onValueChange={(v: 'movie' | 'series') => setCatalogType(v)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="movie">
                  <div className="flex items-center gap-2">
                    <Film className="h-4 w-4" />
                    Movies
                  </div>
                </SelectItem>
                <SelectItem value="series">
                  <div className="flex items-center gap-2">
                    <Tv className="h-4 w-4" />
                    Series
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Sort By</Label>
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MDBLIST_SORT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Order</Label>
              <Select value={order} onValueChange={(v: 'asc' | 'desc') => setOrder(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">Descending</SelectItem>
                  <SelectItem value="asc">Ascending</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="use-filters"
              checked={useFilters}
              onCheckedChange={(checked) => setUseFilters(checked as boolean)}
            />
            <Label htmlFor="use-filters" className="cursor-pointer">
              Apply your parental filters to this list
            </Label>
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function MDBListConfigComponent({ config, onChange }: ConfigSectionProps) {
  const [showApiKey, setShowApiKey] = useState(false)
  const [isVerifying, setIsVerifying] = useState(false)
  const [isVerified, setIsVerified] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  const [activeTab, setActiveTab] = useState('my-lists')
  const [isLoading, setIsLoading] = useState(false)
  const [myLists, setMyLists] = useState<MDBListApiList[]>([])
  const [topLists, setTopLists] = useState<MDBListApiList[]>([])
  const [searchResults, setSearchResults] = useState<MDBListApiList[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [manualInput, setManualInput] = useState('')

  // Edit dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingList, setEditingList] = useState<MDBListApiList | null>(null)
  const [editingConfig, setEditingConfig] = useState<MDBListItem | undefined>()

  const mdbConfig: MDBListConfig = config.mdb || { ak: '', l: [] }
  const lists = mdbConfig.l || []
  const apiKey = mdbConfig.ak || ''

  // Check if API key exists on mount
  useEffect(() => {
    if (apiKey) {
      verifyApiKey(false)
    }
  }, [])

  const updateConfig = (updates: Partial<MDBListConfig>) => {
    onChange({
      ...config,
      mdb: { ...mdbConfig, ...updates },
    })
  }

  const verifyApiKey = async (showNotification = true) => {
    if (!apiKey) {
      if (showNotification) setVerifyError('Please enter an API key')
      return
    }

    setIsVerifying(true)
    setVerifyError(null)

    try {
      const isValid = await mdblistApi.verifyApiKey(apiKey)
      if (isValid) {
        setIsVerified(true)
        loadMyLists()
      } else {
        setVerifyError('Invalid API key')
        setIsVerified(false)
      }
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : 'Verification failed')
      setIsVerified(false)
    } finally {
      setIsVerifying(false)
    }
  }

  const loadMyLists = async () => {
    setIsLoading(true)
    try {
      const data = await mdblistApi.getUserLists(apiKey)
      setMyLists(data || [])
    } catch {
      setMyLists([])
    } finally {
      setIsLoading(false)
    }
  }

  const loadTopLists = async () => {
    if (topLists.length > 0) return // Already loaded
    setIsLoading(true)
    try {
      const data = await mdblistApi.getTopLists(apiKey)
      setTopLists(data || [])
    } catch {
      setTopLists([])
    } finally {
      setIsLoading(false)
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) return

    setIsLoading(true)
    try {
      const data = await mdblistApi.searchLists(apiKey, searchQuery)
      setSearchResults(data || [])
    } catch {
      setSearchResults([])
    } finally {
      setIsLoading(false)
    }
  }

  const handleManualAdd = async () => {
    if (!manualInput.trim()) return

    // Extract list ID from URL if needed
    const urlOrId = manualInput.trim()
    let listId = urlOrId

    if (urlOrId.includes('mdblist.com/lists/')) {
      listId = urlOrId.split('mdblist.com/lists/')[1].replace(/\/$/, '')
    }

    setIsLoading(true)
    try {
      const listDetails = await mdblistApi.getListDetails(apiKey, listId)
      if (listDetails) {
        openEditDialog(listDetails)
      }
    } catch {
      setVerifyError('Failed to fetch list details. Check the URL or ID.')
    } finally {
      setIsLoading(false)
    }
  }

  const isListAdded = (listId: number) => {
    return lists.some((l) => l.i === listId)
  }

  const openEditDialog = (list: MDBListApiList, existingConfig?: MDBListItem) => {
    setEditingList(list)
    setEditingConfig(existingConfig)
    setEditDialogOpen(true)
  }

  // Helper to get migrated catalog configs (same as CatalogConfig.tsx)
  const getMigratedCatalogConfigs = (): CatalogConfig[] => {
    const existingConfigs = config.cc || []
    if (existingConfigs.length > 0) {
      return existingConfigs
    }
    // Migrate legacy selected_catalogs to new format
    const legacySelected = config.sc || []
    if (legacySelected.length > 0) {
      return legacySelected.map((id: string) => ({ ci: id, en: true }))
    }
    return []
  }

  const handleSaveList = (listConfig: MDBListItem) => {
    // Remove existing entry for this list ID if exists
    const newLists = lists.filter((l) => l.i !== listConfig.i)
    newLists.push(listConfig)

    // Also add to catalog_configs (cc) for catalog integration
    const catalogId = `mdblist_${listConfig.ct}_${listConfig.i}`
    const currentConfigs = getMigratedCatalogConfigs()
    const existingConfig = currentConfigs.find((c) => c.ci === catalogId)

    if (!existingConfig) {
      onChange({
        ...config,
        mdb: { ...mdbConfig, l: newLists },
        cc: [...currentConfigs, { ci: catalogId, en: true }],
        sc: [], // Clear legacy format
      })
    } else {
      // Already exists, just update mdblist
      onChange({
        ...config,
        mdb: { ...mdbConfig, l: newLists },
        cc: currentConfigs,
        sc: [], // Clear legacy format
      })
    }
  }

  const removeList = (listId: number) => {
    const listToRemove = lists.find((l) => l.i === listId)
    const newLists = lists.filter((l) => l.i !== listId)

    // Also remove from catalog_configs (cc)
    if (listToRemove) {
      const catalogId = `mdblist_${listToRemove.ct}_${listToRemove.i}`
      const currentConfigs = getMigratedCatalogConfigs()
      onChange({
        ...config,
        mdb: { ...mdbConfig, l: newLists },
        cc: currentConfigs.filter((c) => c.ci !== catalogId),
        sc: [], // Clear legacy format
      })
    } else {
      updateConfig({ l: newLists })
    }
  }

  const moveList = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1
    if (newIndex < 0 || newIndex >= lists.length) return

    const newLists = [...lists]
    ;[newLists[index], newLists[newIndex]] = [newLists[newIndex], newLists[index]]
    updateConfig({ l: newLists })
  }

  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    if (tab === 'top-lists') {
      loadTopLists()
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <List className="h-5 w-5" />
              MDBList Integration
            </CardTitle>
            <CardDescription>Import movie and TV lists from MDBList.com as catalogs</CardDescription>
          </div>
          <a
            href="https://mdblist.com/preferences/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-primary hover:underline flex items-center gap-1"
          >
            Get API Key
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* API Key Section */}
        <div className="space-y-2">
          <Label>API Key</Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                type={showApiKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => {
                  updateConfig({ ak: e.target.value })
                  setIsVerified(false)
                }}
                placeholder="Enter your MDBList API key"
                autoComplete="off"
                data-form-type="other"
                data-lpignore="true"
                name="mdblist-api-key"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 h-full px-3"
                onClick={() => setShowApiKey(!showApiKey)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
            <Button
              onClick={() => verifyApiKey(true)}
              disabled={isVerifying || !apiKey}
              variant={isVerified ? 'secondary' : 'default'}
            >
              {isVerifying ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : isVerified ? (
                <>
                  <Check className="h-4 w-4 mr-1" />
                  Verified
                </>
              ) : (
                'Verify'
              )}
            </Button>
          </div>

          {verifyError && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{verifyError}</AlertDescription>
            </Alert>
          )}
        </div>

        {/* List Management - only shown when verified */}
        {isVerified && (
          <>
            <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
              <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="my-lists" className="flex items-center gap-1">
                  <User className="h-4 w-4" />
                  <span className="hidden sm:inline">My Lists</span>
                </TabsTrigger>
                <TabsTrigger value="top-lists" className="flex items-center gap-1">
                  <Trophy className="h-4 w-4" />
                  <span className="hidden sm:inline">Top Lists</span>
                </TabsTrigger>
                <TabsTrigger value="search" className="flex items-center gap-1">
                  <Search className="h-4 w-4" />
                  <span className="hidden sm:inline">Search</span>
                </TabsTrigger>
                <TabsTrigger value="add" className="flex items-center gap-1">
                  <PlusCircle className="h-4 w-4" />
                  <span className="hidden sm:inline">Add List</span>
                </TabsTrigger>
              </TabsList>

              <TabsContent value="my-lists" className="mt-4 space-y-2">
                {isLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : myLists.length > 0 ? (
                  myLists.map((list) => (
                    <ListItemCard
                      key={list.id}
                      list={list}
                      isAdded={isListAdded(list.id)}
                      onAdd={() => openEditDialog(list)}
                    />
                  ))
                ) : (
                  <Alert>
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>No lists found. Create lists on MDBList.com first.</AlertDescription>
                  </Alert>
                )}
              </TabsContent>

              <TabsContent value="top-lists" className="mt-4 space-y-2">
                {isLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : topLists.length > 0 ? (
                  topLists.map((list) => (
                    <ListItemCard
                      key={list.id}
                      list={list}
                      isAdded={isListAdded(list.id)}
                      onAdd={() => openEditDialog(list)}
                    />
                  ))
                ) : (
                  <Alert>
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>No top lists available.</AlertDescription>
                  </Alert>
                )}
              </TabsContent>

              <TabsContent value="search" className="mt-4 space-y-4">
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search lists..."
                      className="pl-9"
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      autoComplete="off"
                      data-form-type="other"
                      data-lpignore="true"
                      name="mdblist-search"
                    />
                  </div>
                  <Button onClick={handleSearch} disabled={isLoading}>
                    {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Search'}
                  </Button>
                </div>

                <div className="space-y-2">
                  {searchResults.map((list) => (
                    <ListItemCard
                      key={list.id}
                      list={list}
                      isAdded={isListAdded(list.id)}
                      onAdd={() => openEditDialog(list)}
                    />
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="add" className="mt-4 space-y-4">
                <div className="space-y-2">
                  <Label>MDBList URL or ID</Label>
                  <Input
                    value={manualInput}
                    onChange={(e) => setManualInput(e.target.value)}
                    placeholder="https://mdblist.com/lists/username/list-name or list-id"
                    autoComplete="off"
                    data-form-type="other"
                    data-lpignore="true"
                    name="mdblist-url"
                  />
                  <p className="text-xs text-muted-foreground">Paste the full URL of the list or just the list ID</p>
                </div>
                <Button onClick={handleManualAdd} disabled={isLoading || !manualInput.trim()}>
                  {isLoading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
                  Add List
                </Button>
              </TabsContent>
            </Tabs>

            {/* Selected Lists */}
            {lists.length > 0 && (
              <div className="pt-4 border-t space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Selected Lists</Label>
                  <Badge variant="secondary">{lists.length} lists</Badge>
                </div>

                <div className="space-y-2">
                  {lists.map((list, index) => (
                    <div key={`${list.i}-${list.ct}`} className="flex items-center gap-2 p-3 border rounded-lg">
                      <div className="flex flex-col gap-0.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-5 w-5"
                          onClick={() => moveList(index, 'up')}
                          disabled={index === 0}
                        >
                          <ChevronUp className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-5 w-5"
                          onClick={() => moveList(index, 'down')}
                          disabled={index === lists.length - 1}
                        >
                          <ChevronDown className="h-3 w-3" />
                        </Button>
                      </div>

                      <Badge variant="outline" className="shrink-0">
                        {index + 1}
                      </Badge>

                      <div className="flex-1 min-w-0">
                        <p className="font-medium truncate">{list.t}</p>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span className="flex items-center gap-1">
                            {list.ct === 'movie' ? <Film className="h-3 w-3" /> : <Tv className="h-3 w-3" />}
                            {list.ct === 'movie' ? 'Movies' : 'Series'}
                          </span>
                          <span>Sort: {list.s || 'rank'}</span>
                          {list.uf && (
                            <Badge variant="secondary" className="text-[10px]">
                              Filtered
                            </Badge>
                          )}
                        </div>
                      </div>

                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() =>
                          openEditDialog(
                            {
                              id: list.i,
                              name: list.t,
                              mediatype: list.ct === 'movie' ? 'movie' : 'show',
                              items: 0,
                              likes: 0,
                            },
                            list,
                          )
                        }
                      >
                        <Edit2 className="h-4 w-4" />
                      </Button>

                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        onClick={() => removeList(list.i)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {!isVerified && !apiKey && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              Enter your MDBList API key and click Verify to add custom lists as catalogs.
            </AlertDescription>
          </Alert>
        )}

        {/* Edit Dialog */}
        <EditListDialog
          open={editDialogOpen}
          onOpenChange={setEditDialogOpen}
          list={editingList}
          existingConfig={editingConfig}
          onSave={handleSaveList}
        />
      </CardContent>
    </Card>
  )
}

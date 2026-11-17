"""Small authoring helpers; output remains ordinary flat A2UI catalog messages."""
class UI:
    def __init__(self): self.nodes=[]
    def add(self,id,component,**p): self.nodes.append({'id':id,'component':component,**p});return id
    def text(self,id,text,size='body',tone='normal',**p):return self.add(id,'CText',text=text,size=size,tone=tone,**p)
    def stack(self,id,children,direction='column',gap='md',collapse='never',**p):return self.add(id,'CStack',children=children,direction=direction,gap=gap,collapse=collapse,**p)
    def panel(self,id,children,tone='neutral',treatment='outlined',padding='md',**p):return self.add(id,'CSurface',child=self.stack(id+'-body',children),tone=tone,treatment=treatment,padding=padding,**p)
    def section(self,id,title,children,subtitle='',tone='neutral',**p):return self.add(id,'CSection',title=title,subtitle=subtitle,child=self.stack(id+'-body',children),tone=tone,**p)
    def filters(self,id,label,path,action,children,debounce=250):return self.add(id,'CFilterBar',label=label,value=bind(path),onChange=action,children=children,debounceMs=debounce)
    def row(self,id,children,**p):return self.add(id,'CSurface',child=self.stack(id+'-body',children,gap='sm'),tone='neutral',treatment='row',padding='sm',**p)
    def button(self,id,label,name,context=None,primary=False):return self.add(id,'Button',child=self.text(id+'-label',label),variant='primary' if primary else 'borderless',action={'event':{'name':name,'context':context or {}}})
    def badge(self,id,text,tone='neutral'):return self.add(id,'CBadge',text=text,tone=tone)
    def field(self,id,label,path,kind='text',**p):return self.add(id,'CField',label=label,value=bind(path),kind=kind,error=bind('/errors/'+id),**p)
    def picker(self,id,label,path,options,**p):return self.add(id,'CRecordPicker',label=label,value=bind(path),options=[{'value':value,'label':label} for value,label in options],**p)
    def grid(self,id,columns,rows,**p):return self.add(id,'CDataGrid',columns=[{'label':label,'weight':weight} for label,weight in columns],children=[cell for key,cells in rows for cell in cells],rowKeys=[key for key,cells in rows],**p)
    def nav(self,view):return self.stack('views',[self.button('view-'+v,label,'set_view',{'view':v},view==v) for v,label in [('dossier','01 Dossier'),('pipeline','02 Pipeline'),('briefing','03 Briefing'),('hvac','04 HVAC'),('counter','05 Parts desk'),('receiving','06 Receiving'),('dispatch','07 Dispatch'),('field','08 Field notebook'),('adaptive','09 Adaptive'),('case','10 Case workspace')]],'row','sm',wrap=True)
    def record(self,id,label,kind,key):return self.button(id,label,'open_record',{'kind':kind,'recordId':key})
def bind(path):return {'path':path}

# These helpers only emit portable catalog nodes; both hosts own the behavior.
def scroll_area(ui,id,label,child,height="available",**props):
    return ui.add(id,'CScrollArea',label=label,child=child,height=height,**props)

def record_item(ui,id,label,detail,record_id,selection,on_select,badge=''):
    return ui.add(id,'CRecordItem',label=label,detail=detail,badge=badge,recordId=record_id,selectedId=selection,onSelect=on_select)

def reactive_region(ui,id,label,child,status,revision,**props):
    return ui.add(id,'CReactiveRegion',label=label,child=child,status=status,revision=revision,**props)

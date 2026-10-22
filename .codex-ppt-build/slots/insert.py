import zipfile,copy,posixpath
from lxml import etree as E
from pathlib import Path
src=Path('diagram/AI智能客服-AF06-万佳恒.pptx')
P='http://schemas.openxmlformats.org/presentationml/2006/main'; A='http://schemas.openxmlformats.org/drawingml/2006/main'; R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'; PK='http://schemas.openxmlformats.org/package/2006/relationships'; CT='http://schemas.openxmlformats.org/package/2006/content-types'
ns={'p':P,'a':A}
with zipfile.ZipFile(src) as z: data={n:z.read(n) for n in z.namelist()}
original=dict(data)
with zipfile.ZipFile('.codex-ppt-build/slots/one-slide.pptx') as z:
 data['ppt/slides/slide7.xml']=z.read('ppt/slides/slide1.xml')
 data['ppt/notesSlides/notesSlide7.xml']=z.read('ppt/notesSlides/notesSlide1.xml')
def save(n,e):data[n]=E.tostring(e,encoding='UTF-8',xml_declaration=True)
rels=E.fromstring(data['ppt/slides/_rels/slide3.xml.rels'])
for e in rels:
 if e.get('Type').endswith('/notesSlide'):e.set('Target','../notesSlides/notesSlide7.xml')
save('ppt/slides/_rels/slide7.xml.rels',rels)
notesrels=E.fromstring(data['ppt/notesSlides/_rels/notesSlide3.xml.rels'])
for e in notesrels:
 if e.get('Type').endswith('/slide'):e.set('Target','../slides/slide7.xml')
save('ppt/notesSlides/_rels/notesSlide7.xml.rels',notesrels)
prels=E.fromstring(data['ppt/_rels/presentation.xml.rels'])
E.SubElement(prels,'{'+PK+'}Relationship',Id='RSlotFilling',Type=R+'/slide',Target='slides/slide7.xml')
save('ppt/_rels/presentation.xml.rels',prels)
pres=E.fromstring(data['ppt/presentation.xml']); ids=pres.find('{'+P+'}sldIdLst')
new=E.Element('{'+P+'}sldId',id='262');new.set('{'+R+'}id','RSlotFilling');ids.insert(4,new);save('ppt/presentation.xml',pres)
ct=E.fromstring(data['[Content_Types].xml'])
for name,typ in [('slides/slide7.xml','slide'),('notesSlides/notesSlide7.xml','notesSlide')]:E.SubElement(ct,'{'+CT+'}Override',PartName='/ppt/'+name,ContentType='application/vnd.openxmlformats-officedocument.presentationml.'+typ+'+xml')
save('[Content_Types].xml',ct)
lookup={r.get('Id'):r.get('Target') for r in prels}
for i,e in enumerate(ids,1):
 target=lookup[e.get('{'+R+'}id')]; name=target.lstrip('/') if target.startswith('/') else 'ppt/'+target; tree=E.fromstring(data[name])
 for sh in tree.findall('.//p:sp',ns):
  off=sh.find('p:spPr/a:xfrm/a:off',ns)
  if off is not None and int(off.get('x'))>10000000 and int(off.get('y'))>6400000:
   texts=sh.findall('.//a:t',ns)
   if texts:
    texts[0].text=f'{i} / 7'
    for x in texts[1:]:x.text=''
 save(name,tree)
 # verify original content unchanged except footer
 if name in original:
  def nonfooter(b):
   rt=E.fromstring(b)
   for sh in rt.findall('.//p:sp',ns):
    off=sh.find('p:spPr/a:xfrm/a:off',ns)
    if off is not None and int(off.get('x'))>10000000 and int(off.get('y'))>6400000:sh.getparent().remove(sh)
   return E.tostring(rt,method='c14n')
  assert nonfooter(original[name])==nonfooter(data[name]),name
if 'docProps/app.xml' in data:
 app=E.fromstring(data['docProps/app.xml'])
 for e in app.iter():
  if E.QName(e).localname=='Slides':e.text='7'
 save('docProps/app.xml',app)
with zipfile.ZipFile('.codex-ppt-build/slots/candidate.pptx','w',zipfile.ZIP_DEFLATED) as z:
 for n,b in data.items():z.writestr(n,b)
print('7 slides; inserted at position 5; original slide contents verified unchanged except pagination')

from ai_shorts_studio.domain.model import Clip
from ai_shorts_studio.media.render import Renderer

class ProxyRenderer(Renderer):
    def render_proxy(self,source,metadata,destination,progress):
        factor = min(1,1280/max(metadata['width'],metadata['height']))
        w,h = [max(2,round(metadata[k]*factor/2)*2) for k in ('width','height')]
        clip = Clip('proxy',0,metadata['duration_us'],width=w,height=h,fps=min(30,metadata['fps']),
                    audio={str(metadata['audio'][0]['index']):1} if metadata['audio'] else {})
        return self.render(source,clip,metadata,destination,progress=progress,_proxy=True)

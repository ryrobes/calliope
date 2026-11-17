"""Server Sketch DSL through the real Excalidraw loader and text editor.

Requires calliope-sketch's npm dependencies and Playwright/Chromium. All
fixtures and font requests are local; no notebooks or databases are modified.
"""
import base64
import json
from pathlib import Path
import subprocess

import calliope
import briarpatch
import pytest

ROOT = Path(__file__).parent


@pytest.fixture(scope="module")
def sketch_bundle(tmp_path_factory):
    bundle = tmp_path_factory.mktemp("sketch") / "probe.js"
    entry = '''
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import {Excalidraw,convertToExcalidrawElements,restoreElements,exportToSvg,exportToBlob,FONT_FAMILY} from '@excalidraw/excalidraw';
      import {materializeSketchElements} from './src/scene';
      window.qa={load:materializeSketchElements,convert:convertToExcalidrawElements,restore:restoreElements,
        exportToSvg,exportToBlob,FONT_FAMILY,
        mount(elements) { createRoot(document.getElementById('root')).render(
          React.createElement(Excalidraw, {initialData:{elements,appState:{
            scrollX:0,scrollY:0,zoom:{value:1},currentItemFontFamily:12,viewBackgroundColor:'#ffffff'}},
            excalidrawAPI:api=>window.qa.api=api})); }};
    '''
    subprocess.run(["node", "--input-type=module", "-e", f'''
      import {{build}} from 'esbuild';
      import {{readFile}} from 'node:fs/promises';
      import {{patchSuperiorFont}} from './scripts/superior-font.mjs';
      await build({{stdin:{{contents:{json.dumps(entry)},resolveDir:process.cwd(),loader:'tsx'}},
        bundle:true,format:'iife',outfile:{json.dumps(str(bundle))},
        define:{{'process.env.NODE_ENV':'"production"'}},
        loader:{{'.woff2':'dataurl','.css':'empty'}},
        plugins:[{{name:'superior',setup(build){{
          build.onLoad({{filter:/[\\/]@excalidraw[\\/]excalidraw[\\/]dist[\\/].*\\.js$/}},async args=>{{
            const source=await readFile(args.path,'utf8');
            return {{contents:patchSuperiorFont(source)??source,loader:'js'}};
          }});
        }}}}]}});
    '''], cwd=ROOT / "calliope-sketch", check=True)
    return bundle


@pytest.fixture
def sketch_page(sketch_bundle):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                                   args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route_asset(route):
            prefix = "http://sketch.test/calliope/sketch-assets/"
            if route.request.url.startswith(prefix):
                asset = ROOT / "calliope/sketch-assets" / route.request.url[len(prefix):]
                if asset.is_file() and asset.resolve().is_relative_to(ROOT):
                    route.fulfill(body=asset.read_bytes(), content_type="font/woff2")
                    return
            route.abort()
        page.route("**/*", route_asset)
        page.goto("about:blank")
        page.set_content('<div id="root" style="width:1200px;height:800px"></div>')
        page.add_style_tag(path=str(ROOT / "calliope/sketch-runtime.css"))
        page.evaluate("window.EXCALIDRAW_ASSET_PATH='http://sketch.test/calliope/sketch-assets/'")
        fonts = [[family, base64.b64encode(path.read_bytes()).decode()]
                 for folder, family in [("Excalifont", "Excalifont"), ("Superior", "Superior")]
                 for path in (ROOT / "calliope/sketch-assets/fonts" / folder).glob("*.woff2")]
        page.evaluate('''async fonts => {
          for (const [family,data] of fonts) {
            const face=new FontFace(family,`url(data:font/woff2;base64,${data})`);
            await face.load();document.fonts.add(face);
          }
        }''', fonts)
        page.add_script_tag(path=str(sketch_bundle))
        yield page
        browser.close()
        assert errors == []


def server_scene(shape="rectangle", label="Business domains", **style):
    if shape == "arrow":
        elements, _ = calliope._apply_sketch_operations([], [
            {"op": "add_shape", "id": "start", "shape": "rectangle", "x": 180, "y": 220},
            {"op": "add_shape", "id": "end", "shape": "rectangle", "x": 750, "y": 220},
            {"op": "connect", "id": "domain", "from_id": "start", "to_id": "end", "label": label},
        ], 1)
        return {"elements": elements}
    elements, _ = calliope._apply_sketch_operations([], [{
        "op": "add_shape", "id": "domain", "shape": shape,
        "x": 300, "y": 200, "width": 240, "height": 140,
        "label": label, "style": style,
    }], 1)
    return {"elements": elements}


@pytest.mark.parametrize("shape,fixed_width", [
    ("rectangle", False), ("diamond", False), ("ellipse", False), ("arrow", False),
    ("rectangle", True),
])
def test_bound_label_already_matches_no_op_text_edit(sketch_page, shape, fixed_width):
    page = sketch_page
    scene = server_scene(shape, "Business domains\nCustomer relationships")
    if fixed_width:
        label = next(e for e in scene["elements"] if e["type"] == "text")
        label.update(autoResize=False, width=210)
    page.evaluate('scene=>{window.scene=qa.load(scene);qa.mount(window.scene)}',
                  scene)
    page.wait_for_function("qa.api?.getSceneElements().some(e=>e.type==='text')")
    page.wait_for_timeout(300)
    before = page.evaluate('''() => {
      const t=qa.api.getSceneElements().find(e=>e.type==='text');
      const s=qa.api.getAppState();
      return {text:t,click:{x:(t.x+t.width/2+s.scrollX)*s.zoom.value+s.offsetLeft,
        y:(t.y+t.height/2+s.scrollY)*s.zoom.value+s.offsetTop}};
    }''')
    page.mouse.dblclick(**before["click"])
    page.wait_for_selector("textarea.excalidraw-wysiwyg")
    page.keyboard.press("Escape")
    page.wait_for_selector("textarea.excalidraw-wysiwyg", state="detached")
    after = page.evaluate("qa.api.getSceneElements().find(e=>e.type==='text')")
    if fixed_width:
        assert before["text"]["width"] == 210
    for key in ("x", "y", "width", "height"):
        assert after[key] == pytest.approx(before["text"][key], abs=0.01)
    assert after["text"] == before["text"]["text"]
    page.screenshot(path=f"/tmp/calliope-sketch-label-{shape}.png")


def test_repair_saved_offset_and_repeated_mixed_scenes(sketch_page):
    result = sketch_page.evaluate('''scene=>{
      const untouched=JSON.stringify(scene);
      const good=qa.load(scene), damaged=structuredClone(good);
      const label=damaged.find(e=>e.type==='text');
      label.x-=label.width;label.y-=label.height;
      let repaired=qa.load({elements:damaged});
      const repairedOnce=structuredClone(repaired);
      for(let n=0;n<4;n++) repaired=qa.load({elements:[...repaired,
        {id:`new-${n}`,type:'rectangle',x:800,y:n*150,width:200,height:100,
          label:{text:`New agent work ${n}`}}]});
      return {good,repairedOnce,repaired,unchanged:JSON.stringify(scene)===untouched};
    }''', server_scene())
    assert result["unchanged"]
    for element in result["good"]:
        assert next(e for e in result["repairedOnce"] if e["id"] == element["id"]) == element
        assert next(e for e in result["repaired"] if e["id"] == element["id"]) == element
    elements = result["repaired"]
    assert len(elements) == len({e["id"] for e in elements}) == 10
    for text in (e for e in elements if e["type"] == "text"):
        container = next(e for e in elements if e["id"] == text["containerId"])
        assert {"id": text["id"], "type": "text"} in container["boundElements"]


def test_mixed_scene_preserves_native_text_frames_images_and_routes(sketch_page):
    result = sketch_page.evaluate('''scene=>{
      const saved=qa.load(scene);
      const other=qa.restore(qa.convert([
        {id:'note',type:'text',x:650,y:400,text:'Human note',textAlign:'right'},
        {id:'photo',type:'image',x:10,y:10,width:80,height:80,fileId:'avatar'},
        {id:'frame',type:'frame',x:0,y:0,width:1100,height:700,children:[]},
        {id:'route',type:'arrow',x:10,y:600,points:[[0,0],[100,-40],[220,0]]},
      ],{regenerateIds:false}),null,{repairBindings:true});
      other[0]={...other[0],autoResize:false,width:320,frameId:'frame',
        customData:{human:true},groupIds:['human-group']};
      other[1]={...other[1],crop:{x:1,y:2,width:50,height:60,naturalWidth:80,naturalHeight:80}};
      // A real saved scene has globally ordered indices; concatenating two
      // independent constructor outputs would give it duplicate indices.
      const original=qa.restore([...saved,...other],null,{repairBindings:true});
      const loaded=qa.load({elements:[...original,
        {id:'new',type:'rectangle',x:700,y:20,width:200,height:100},
        {id:'link',type:'arrow',x:540,y:250,start:{id:'domain'},end:{id:'new'},label:{text:'uses'}},
      ]});
      return {other:original.filter(e=>other.some(o=>o.id===e.id)),loaded};
    }''', server_scene())
    for original in result["other"]:
        loaded = next(e for e in result["loaded"] if e["id"] == original["id"])
        for key, value in original.items():
            if key not in ("index", "boundElements"):
                assert loaded[key] == value, (original["id"], key)
    domain = next(e for e in result["loaded"] if e["id"] == "domain")
    assert {"id": "link", "type": "arrow"} in domain["boundElements"]
    link = next(e for e in result["loaded"] if e["id"] == "link")
    assert link["startBinding"]["elementId"] == "domain"
    assert link["endBinding"]["elementId"] == "new"


@pytest.mark.parametrize("align,vertical", [("left", "top"), ("center", "middle"), ("right", "bottom")])
def test_alignment_and_legacy_semantic_labels(sketch_page, align, vertical):
    result = sketch_page.evaluate('''scene=>{
      const first=qa.load(scene);
      const second=qa.load({elements:first});
      return {first,second};
    }''', {"elements": [{"id": "legacy", "type": "rectangle", "x": 300, "y": 200,
                         "width": 240, "height": 140,
                         "label": {"text": "One\nTwo", "textAlign": align, "verticalAlign": vertical}}]})
    assert result["first"] == result["second"]
    text = next(e for e in result["first"] if e["type"] == "text")
    assert text["textAlign"] == align
    assert text["verticalAlign"] == vertical
    assert 300 <= text["x"] <= 540 - text["width"]
    assert 200 <= text["y"] <= 340 - text["height"]


def test_superior_font_and_server_measurements_agree(sketch_page):
    samples = ["Business domains", "AVATAR / Revenue", "Office efficiency", "Customer relationships"]
    widths = sketch_page.evaluate('''texts=>{
      if(qa.FONT_FAMILY.Superior!==12) throw Error('Superior is not registered');
      const canvas=document.createElement('canvas'), ctx=canvas.getContext('2d');
      ctx.font='20px Superior';
      return texts.map(text=>{
        const [element]=qa.convert([{type:'text',x:0,y:0,text,fontSize:20,fontFamily:12}]);
        return {width:element.width,actual:ctx.measureText(text).width};
      });
    }''', samples)
    assert calliope._sketch_superior_font() is not None
    for text, measured in zip(samples, widths):
        assert measured["width"] == pytest.approx(measured["actual"], abs=0.02)
        assert calliope._sketch_measure_text(text, 20) == pytest.approx(measured["actual"], abs=0.1)


def test_superior_svg_and_png_exports_embed_and_render_font(sketch_page):
    result = sketch_page.evaluate('''async scene=>{
      const elements=qa.load(scene);
      const options={elements,files:{},appState:{exportBackground:true,
        viewBackgroundColor:'#ffffff',exportWithDarkMode:false}};
      const svg=await qa.exportToSvg(options);
      const markup=new XMLSerializer().serializeToString(svg);
      const png=await qa.exportToBlob({...options,mimeType:'image/png'});
      const image=new Image();image.src=URL.createObjectURL(png);await image.decode();
      document.getElementById('root').replaceChildren(svg);
      return {markup,pngType:png.type,pngBytes:png.size,width:image.width};
    }''', server_scene(label="Business domains"))
    assert 'font-family="Superior' in result["markup"]
    assert "@font-face" in result["markup"]
    assert "data:font/woff2;base64," in result["markup"]
    assert result["pngType"] == "image/png" and result["pngBytes"] > 1000
    assert result["width"] > 200
    sketch_page.screenshot(path="/tmp/calliope-superior-svg.png")


def test_superior_is_selectable_in_editor(sketch_page):
    page = sketch_page
    page.evaluate("scene=>qa.mount(qa.load(scene))", server_scene())
    page.wait_for_function("qa.api?.getSceneElements().length===2")
    assert page.evaluate("qa.api.getAppState().currentItemFontFamily") == 12
    page.evaluate("qa.api.updateScene({appState:{selectedElementIds:{domain:true}}})")
    page.get_by_test_id("font-family-hand-drawn").click()
    page.wait_for_function("qa.api.getSceneElements().find(e=>e.type==='text').fontFamily===5")
    page.get_by_test_id("font-family-show-fonts").click()
    page.get_by_text("Superior", exact=True).click()
    page.wait_for_function("qa.api.getSceneElements().find(e=>e.type==='text').fontFamily===12")
    page.screenshot(path="/tmp/calliope-superior-editor.png")


def test_font_defaults_and_explicit_handwriting_survive_reload(sketch_page):
    default = server_scene()
    explicit = server_scene(font_family=5)
    assert next(e for e in default["elements"] if e["type"] == "text")["fontFamily"] == 12
    assert next(e for e in explicit["elements"] if e["type"] == "text")["fontFamily"] == 5
    result = sketch_page.evaluate('''scene=>{
      const first=qa.load(scene);return qa.load({elements:first});
    }''', explicit)
    assert next(e for e in result if e["type"] == "text")["fontFamily"] == 5
    elements, _ = calliope._apply_sketch_operations([], [
        {"op": "add_text", "id": "title", "text": "Title"},
        {"op": "add_shape", "id": "one", "shape": "rectangle"},
        {"op": "add_shape", "id": "two", "shape": "rectangle", "x": 400},
        {"op": "connect", "id": "link", "from_id": "one", "to_id": "two", "label": "uses"},
    ], 1)
    assert next(e for e in elements if e["id"] == "title")["fontFamily"] == 12
    assert next(e for e in elements if e["id"] == "link")["label"]["fontFamily"] == 12


def test_managed_whiteboard_font_reconciles_once():
    class EmptyMap:
        def execute(self, *args):
            return self
        def fetchall(self):
            return []
        def fetchone(self):
            return {"r": False}
    desired, _ = briarpatch._desired_model(EmptyMap())
    assert all(spec["style"]["font_family"] == 12 for spec in desired.values()
               if spec.get("label") or spec["op"] == "add_text")
    scene = server_scene(label="Domain", font_family=5)["elements"]
    wanted = {"domain": {"op": "add_shape", "shape": "rectangle", "label": "Domain",
                         "style": {"font_family": 12}}}
    ops = briarpatch._diff_operations(scene, wanted)
    assert ops == [{"op": "style", "id": "domain", "style": {"font_family": 12}}]
    updated, _ = calliope._apply_sketch_operations(scene, ops, 2)
    assert briarpatch._diff_operations(updated, wanted) == []
    assert next(e for e in updated if e["type"] == "text")["fontFamily"] == 12

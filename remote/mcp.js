/*
 * LibreSprite MCP bridge - SAFE by default.
 * GPL-2.0, derived conceptually from Snehil-Shah/LibreSprite-MCP.
 * SAFE mode dispatches only named operations. Arbitrary JavaScript is accepted only
 * when the relay explicitly reports DEV mode.
 */
const global = this;
(function LibreSpriteMCPBridge() {
  const BRIDGE_VERSION = "0.2.0-safe";
  const BASE = "http://127.0.0.1:64823";
  const POLL_DELAY = 30;
  let active = false, polling = false, token = null, mode = "safe";
  let getCb = null, postCb = null, dialog = null;

  const log = function() { try { global.console.log.apply(global.console, arguments); } catch (_) {} };

  function fetchGet(url, cb) {
    storage.fetch(url, "_mcp_get");
    getCb = function() { cb(storage.get("_mcp_get"), storage.get("_mcp_get_status")); };
  }
  function fetchPost(url, body, cb, auth) {
    const args = [url, "_mcp_post", "", "POST", body, "Content-Type", "application/json"];
    if (auth && token) { args.push("X-LibreSprite-Token", token); }
    storage.fetch.apply(storage, args);
    postCb = function() { cb(storage.get("_mcp_post"), storage.get("_mcp_post_status")); };
  }
  function jsonGet(url, cb) {
    fetchGet(url, function(text, status) {
      if (status !== 200) return cb(null, "HTTP " + status);
      try { cb(JSON.parse(text), null); } catch (e) { cb(null, String(e)); }
    });
  }
  function jsonPost(url, obj, cb, auth) {
    fetchPost(url, JSON.stringify(obj), function(text, status) {
      if (status < 200 || status >= 300) return cb(null, "HTTP " + status + " " + text);
      try { cb(JSON.parse(text), null); } catch (e) { cb(null, String(e)); }
    }, auth);
  }
  function pair() {
    if (typeof storage === "undefined" || typeof storage.fetch !== "function") {
      log("LibreSprite MCP: storage.fetch is unavailable in this build.");
      return;
    }
    jsonPost(BASE + "/pair", {
      bridge_version: BRIDGE_VERSION,
      libresprite_version: String(app.version || ""),
      platform: String(app.platform || ""),
      storage: typeof storage !== "undefined",
      storage_fetch: typeof storage !== "undefined" && typeof storage.fetch === "function"
    }, function(data, err) {
      if (err) { app.yield("retry", POLL_DELAY); return; }
      token = data.session_token;
      mode = data.mode || "safe";
      polling = true;
      paintUI();
      app.yield("poll");
    }, false);
  }
  function authGetNext(cb) { jsonGet(BASE + "/next?token=" + encodeURIComponent(token || ""), cb); }
  function postResult(result) {
    jsonPost(BASE + "/result", result, function(_, err) {
      if (err) log("LibreSprite MCP result post failed:", err);
      if (polling) app.yield("poll", POLL_DELAY);
    }, true);
  }
  function ok(requestId, result) { postResult({protocol_version:1, request_id:requestId, ok:true, result:result || {}}); }
  function fail(requestId, code, message, details) {
    postResult({protocol_version:1, request_id:requestId, ok:false,
      error:{code:code, message:message, details:details || {}}});
  }
  function requireSprite() { if (!app.activeSprite) throw new Error("NO_ACTIVE_SPRITE"); return app.activeSprite; }
  function layerAt(i) {
    const s=requireSprite();
    if (!Number.isInteger(i) || i < 0 || i >= s.layerCount) throw new Error("LAYER_OUT_OF_RANGE");
    const l=s.layer(i); if (!l || !l.isImage) throw new Error("LAYER_NOT_IMAGE");
    return l;
  }
  function celAt(layer, frame) { const l=layerAt(layer); const c=l.cel(frame); if (!c) throw new Error("CEL_NOT_FOUND"); return c; }
  function gotoFrame(frame) {
    if (!Number.isInteger(frame) || frame < 0) throw new Error("FRAME_OUT_OF_RANGE");
    app.command.clearParameters();
    app.command.setParameter("frame", String(frame + 1));
    app.command.GotoFrame();
    app.command.clearParameters();
    if (app.activeFrameNumber !== frame) throw new Error("FRAME_OUT_OF_RANGE");
  }
  function countFrames() {
    requireSprite();
    const original=app.activeFrameNumber;
    app.command.GotoFirstFrame();
    const first=app.activeFrameNumber;
    let count=1, guard=0;
    while (guard++ < 4096) {
      app.command.GotoNextFrame();
      if (app.activeFrameNumber === first) break;
      count++;
    }
    gotoFrame(Math.min(original, count-1));
    return count;
  }
  function rgbaObj(color) {
    return {r:app.pixelColor.rgbaR(color), g:app.pixelColor.rgbaG(color),
            b:app.pixelColor.rgbaB(color), a:app.pixelColor.rgbaA(color)};
  }
  function rgbaVal(p) { return app.pixelColor.rgba(p.r|0,p.g|0,p.b|0,(p.a===undefined?255:p.a)|0); }
  function bytesFromImage(img) { const raw=img.getImageData(), out=[]; for (let i=0;i<raw.length;i++) out.push(raw[i]); return out; }
  function drawPixels(p) {
    const c=celAt(p.layer,p.frame), img=c.image;
    if (!Array.isArray(p.pixels) || p.pixels.length > 262144) throw new Error("PIXEL_LIMIT");
    for (let i=0;i<p.pixels.length;i++) {
      const q=p.pixels[i];
      if (q.x<0||q.y<0||q.x>=img.width||q.y>=img.height) continue;
      img.putPixel(q.x,q.y,rgbaVal(q));
    }
    requireSprite().commit();
    return {written:p.pixels.length};
  }
  function pixelsRegion(p) {
    const img=celAt(p.layer,p.frame).image, arr=[];
    for (let yy=0;yy<p.height;yy++) for (let xx=0;xx<p.width;xx++) {
      const x=p.x+xx,y=p.y+yy;
      if (x<0||y<0||x>=img.width||y>=img.height) arr.push({r:0,g:0,b:0,a:0});
      else arr.push(rgbaObj(img.getPixel(x,y)));
    }
    return {x:p.x,y:p.y,width:p.width,height:p.height,pixels:arr};
  }
  function setRegion(p) {
    if (!Array.isArray(p.rgba) || p.rgba.length !== p.width*p.height*4) throw new Error("RGBA_LENGTH_MISMATCH");
    const img=celAt(p.layer,p.frame).image; let k=0,written=0;
    for(let yy=0;yy<p.height;yy++) for(let xx=0;xx<p.width;xx++) {
      const q={r:p.rgba[k++],g:p.rgba[k++],b:p.rgba[k++],a:p.rgba[k++]};
      const x=p.x+xx,y=p.y+yy;
      if(x>=0&&y>=0&&x<img.width&&y<img.height){img.putPixel(x,y,rgbaVal(q));written++;}
    }
    requireSprite().commit(); return {written:written};
  }
  function copyOrMoveRegion(p, move) {
    const img=celAt(p.layer,p.frame).image, tmp=[];
    for(let yy=0;yy<p.height;yy++) for(let xx=0;xx<p.width;xx++) {
      const x=p.x+xx,y=p.y+yy;
      tmp.push((x>=0&&y>=0&&x<img.width&&y<img.height)?img.getPixel(x,y):app.pixelColor.rgba(0,0,0,0));
    }
    if(move) for(let yy=0;yy<p.height;yy++) for(let xx=0;xx<p.width;xx++) {
      const x=p.x+xx,y=p.y+yy;if(x>=0&&y>=0&&x<img.width&&y<img.height)img.putPixel(x,y,app.pixelColor.rgba(0,0,0,0));
    }
    let k=0;
    for(let yy=0;yy<p.height;yy++) for(let xx=0;xx<p.width;xx++) {
      const x=p.dest_x+xx,y=p.dest_y+yy,v=tmp[k++];
      if(x>=0&&y>=0&&x<img.width&&y<img.height)img.putPixel(x,y,v);
    }
    requireSprite().commit(); return {ok:true};
  }
  function clearRegion(p) {
    const img=celAt(p.layer,p.frame).image, clear=app.pixelColor.rgba(0,0,0,0); let n=0;
    for(let yy=0;yy<p.height;yy++)for(let xx=0;xx<p.width;xx++){
      const x=p.x+xx,y=p.y+yy;if(x>=0&&y>=0&&x<img.width&&y<img.height){img.putPixel(x,y,clear);n++;}
    }
    requireSprite().commit(); return {cleared:n};
  }
  function flipRegion(p) {
    const img=celAt(p.layer,p.frame).image,tmp=[];
    for(let yy=0;yy<p.height;yy++)for(let xx=0;xx<p.width;xx++){
      const x=p.x+xx,y=p.y+yy;tmp.push((x>=0&&y>=0&&x<img.width&&y<img.height)?img.getPixel(x,y):app.pixelColor.rgba(0,0,0,0));
    }
    for(let yy=0;yy<p.height;yy++)for(let xx=0;xx<p.width;xx++){
      const sx=p.horizontal?(p.width-1-xx):xx, sy=p.horizontal?yy:(p.height-1-yy);
      const x=p.x+xx,y=p.y+yy;if(x>=0&&y>=0&&x<img.width&&y<img.height)img.putPixel(x,y,tmp[sy*p.width+sx]);
    }
    requireSprite().commit(); return {ok:true};
  }
  function previewFrame(frame) {
    const s=requireSprite(), layers=[];
    for(let i=0;i<s.layerCount;i++){
      const l=s.layer(i); if(!l || !l.isImage || !l.isVisible) continue;
      const c=l.cel(frame); if(!c || !c.image) continue;
      layers.push({layer:i,x:c.x,y:c.y,png_data_uri:c.image.getPNGData()});
    }
    return {frame:frame,canvas_width:s.width,canvas_height:s.height,layers:layers};
  }
  function health() {
    return {
      connected:true, bridge_version:BRIDGE_VERSION, libresprite_version:String(app.version||""),
      platform:String(app.platform||""), mode:mode,
      capabilities:{frame_duration_read:false,frame_duration_write:false,frame_tags:false,export_spritesheet:false,
        preview:true,pixels:true,layers:true,cels:true,frames:true,arbitrary_script:(mode==="dev")},
      limitations:{
        frame_duration:"Upstream scripting exposes FrameProperties as UI-only and no direct frame-duration API.",
        frame_tags:"Current scripting reference exposes commands but no non-interactive tag object API.",
        export_spritesheet:"ExportSpriteSheet is exposed as a GUI command without a verified non-interactive parameter API."
      }
    };
  }
  function dispatch(op,p) {
    switch(op) {
      case "health": return health();
      case "get_sprite_info": { const s=requireSprite(); return {width:s.width,height:s.height,filename:s.filename,
        color_mode:s.colorMode,layer_count:s.layerCount,active_frame:app.activeFrameNumber,active_layer:app.activeLayerNumber,frame_count:countFrames()}; }
      case "get_active_frame": return {frame:app.activeFrameNumber};
      case "set_active_frame": gotoFrame(p.frame); return {frame:app.activeFrameNumber};
      case "get_frames": {const n=countFrames(); return {count:n,frames:Array.from({length:n},(_,i)=>i)};}
      case "get_layers": {const s=requireSprite(),a=[];for(let i=0;i<s.layerCount;i++){const l=s.layer(i);a.push({index:i,name:l.name,
        visible:l.isVisible,editable:l.isEditable,image:l.isImage,background:l.isBackground,cel_count:l.celCount});}return {layers:a};}
      case "add_frame": {const before=countFrames();app.command.clearParameters();app.command.setParameter("content","empty");app.command.NewFrame();app.command.clearParameters();const after=countFrames();return {before:before,after:after,frame:app.activeFrameNumber};}
      case "insert_frame": {gotoFrame(p.frame);const before=countFrames();app.command.clearParameters();app.command.setParameter("content","empty");app.command.NewFrame();app.command.clearParameters();const after=countFrames();return {before:before,after:after,frame:app.activeFrameNumber};}
      case "duplicate_frame": {gotoFrame(p.frame);const before=countFrames();app.command.clearParameters();app.command.setParameter("content","frame");app.command.NewFrame();app.command.clearParameters();const after=countFrames();return {before:before,after:after,frame:app.activeFrameNumber};}
      case "delete_frame": {gotoFrame(p.frame);const before=countFrames();app.command.RemoveFrame();const after=countFrames();return {before:before,after:after,frame:app.activeFrameNumber};}
      case "get_frame_durations":
      case "set_frame_duration":
      case "set_frame_durations": return {ok:false,unsupported:true,code:"UNSUPPORTED_FRAME_DURATION",message:health().limitations.frame_duration};
      case "create_layer": {const s=requireSprite(),before=s.layerCount;app.command.NewLayer();const after=s.layerCount;if(after!==before+1)throw new Error("POSTCONDITION_FAILED");const l=s.layer(after-1);l.name=String(p.name||"Layer");s.commit();return {layer:after-1,layer_count:after,name:l.name};}
      case "delete_layer": {const s=requireSprite(),before=s.layerCount;app.activeLayerNumber=p.layer;app.command.RemoveLayer();const after=s.layerCount;if(after!==before-1)throw new Error("POSTCONDITION_FAILED");return {layer_count:after};}
      case "rename_layer": {const l=layerAt(p.layer);l.name=String(p.name);requireSprite().commit();return {layer:p.layer,name:l.name};}
      case "set_layer_visibility": {const l=layerAt(p.layer);l.isVisible=!!p.visible;requireSprite().commit();return {layer:p.layer,visible:l.isVisible};}
      case "set_active_layer": {layerAt(p.layer);app.activeLayerNumber=p.layer;return {layer:app.activeLayerNumber};}
      case "get_cel": {const c=celAt(p.layer,p.frame);return {layer:p.layer,frame:p.frame,x:c.x,y:c.y,width:c.image.width,height:c.image.height};}
      case "copy_cel": {gotoFrame(p.source_frame);app.activeLayerNumber=p.source_layer;app.command.clearParameters();
        app.command.setParameter("srcFrame",String(p.source_frame+1));app.command.setParameter("dstFrame",String(p.target_frame+1));
        app.command.setParameter("srcLayer",String(p.source_layer));app.command.setParameter("dstLayer",String(p.target_layer));
        app.command.CopyCel();app.command.clearParameters();return {copied:true};}
      case "move_cel": {const c=celAt(p.layer,p.frame);c.setPosition(p.x,p.y);requireSprite().commit();return {x:c.x,y:c.y};}
      case "clear_cel": {gotoFrame(p.frame);app.activeLayerNumber=p.layer;app.command.ClearCel();return {cleared:true};}
      case "get_pixel": {const img=celAt(p.layer,p.frame).image;if(p.x<0||p.y<0||p.x>=img.width||p.y>=img.height)throw new Error("PIXEL_OUT_OF_RANGE");return Object.assign({x:p.x,y:p.y},rgbaObj(img.getPixel(p.x,p.y)));}
      case "get_pixels":
      case "get_region": return pixelsRegion(p);
      case "draw_pixels": return drawPixels(p);
      case "set_region": return setRegion(p);
      case "copy_region": return copyOrMoveRegion(p,false);
      case "move_region": return copyOrMoveRegion(p,true);
      case "clear_region": return clearRegion(p);
      case "flip_region": return flipRegion(p);
      case "get_image_data": {const img=celAt(p.layer,p.frame).image;return {width:img.width,height:img.height,stride:img.stride,format:img.format,bytes:bytesFromImage(img)};}
      case "set_image_data": {const img=celAt(p.layer,p.frame).image;if(!Array.isArray(p.rgba)||p.rgba.length!==img.stride*img.height)throw new Error("IMAGE_DATA_LENGTH_MISMATCH");
        const a=new Uint8Array(p.rgba);img.putImageData(a);requireSprite().commit();return {bytes:p.rgba.length};}
      case "get_palette": {const pal=requireSprite().palette,a=[];for(let i=0;i<pal.length;i++)a.push(Object.assign({index:i},rgbaObj(pal.get(i))));return {colors:a};}
      case "set_palette_color": {const pal=requireSprite().palette;if(p.index<0||p.index>=pal.length)throw new Error("PALETTE_INDEX_OUT_OF_RANGE");pal.set(p.index,rgbaVal(p));requireSprite().commit();return Object.assign({index:p.index},rgbaObj(pal.get(p.index)));}
      case "get_tags": return {ok:false,unsupported:true,code:"UNSUPPORTED_FRAME_TAGS",message:health().limitations.frame_tags};
      case "create_tag":
      case "delete_tag": return {ok:false,unsupported:true,code:"UNSUPPORTED_FRAME_TAGS",message:health().limitations.frame_tags};
      case "render_frame_preview": gotoFrame(p.frame); return previewFrame(p.frame);
      case "save_sprite": requireSprite().save(); return {saved:true,filename:requireSprite().filename};
      case "save_as": requireSprite().saveAs(String(p.path),false); return {saved:true,filename:requireSprite().filename,path:String(p.path)};
      case "open_sprite": app.open(String(p.path)); return {opened:true,path:String(p.path),filename:requireSprite().filename};
      case "export_png": if(p.frame!==null&&p.frame!==undefined)gotoFrame(p.frame);requireSprite().saveAs(String(p.path),true);return {exported:true,path:String(p.path)};
      case "export_gif": requireSprite().saveAs(String(p.path),true);return {exported:true,path:String(p.path)};
      case "export_spritesheet": return {ok:false,unsupported:true,code:"UNSUPPORTED_SPRITESHEET_EXPORT",message:health().limitations.export_spritesheet};
      case "run_script":
        if(mode!=="dev") throw new Error("DEV_MODE_REQUIRED");
        return {output:(new Function("return (function(){"+String(p.script)+"\n}).call(this);"))()};
      default: throw new Error("OPERATION_NOT_ALLOWED");
    }
  }
  function execute(req) {
    const rid=req.request_id, op=req.operation, payload=req.payload||{};
    try {
      if(op==="run_script" && mode!=="dev") return fail(rid,"DEV_MODE_REQUIRED","run_script is unavailable in SAFE mode");
      const result=dispatch(op,payload); ok(rid,result);
    } catch(e) { fail(rid,String(e.message||e).replace(/\s+/g,"_").toUpperCase(),String(e.message||e)); }
  }
  function poll() {
    if(!polling||!token)return;
    authGetNext(function(data,err){
      if(err){polling=false;paintUI();app.yield("retry",POLL_DELAY);return;}
      if(data&&data.idle){app.yield("poll",POLL_DELAY);return;}
      if(data&&data.request_id&&data.operation){execute(data);return;}
      app.yield("poll",POLL_DELAY);
    });
  }
  function paintUI() {
    if(dialog)dialog.close();
    dialog=app.createDialog();dialog.title="LibreSprite MCP ("+mode.toUpperCase()+")";
    dialog.addLabel(polling?"Connected - "+mode.toUpperCase()+" mode":"Disconnected");
    dialog.addBreak();
    if(!polling)dialog.addButton("Connect","connect"); else dialog.addButton("Disconnect","disconnect");
  }
  function onEvent(event) {
    switch(event){
      case "init": active=true; paintUI(); return;
      case "_close": active=false;polling=false;token=null;return;
      case "connect_click": pair(); return;
      case "disconnect_click": polling=false;token=null;paintUI();return;
      case "_mcp_get_fetch": if(getCb){const f=getCb;getCb=null;f();} return;
      case "_mcp_post_fetch": if(postCb){const f=postCb;postCb=null;f();} return;
      case "poll": if(active)poll(); return;
      case "retry": if(active&&!polling)paintUI(); return;
    }
  }
  global.onEvent=onEvent;
})();

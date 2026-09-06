/*
 * LibreSprite MCP bridge - SAFE by default.
 * GPL-2.0-only. Derived from Snehil-Shah/LibreSprite-MCP.
 *
 * SAFE mode accepts only named operations dispatched below. Arbitrary JavaScript
 * exists only in explicit DEV mode and is rejected in SAFE mode on both sides.
 */
const global = this;

(function LibreSpriteMCPBridge() {
  const BRIDGE_VERSION = "0.2.1-safe";
  const BASE = "http://127.0.0.1:64823";
  const POLL_DELAY = 30;
  const MAX_PIXELS = 262144;

  let active = false;
  let polling = false;
  let token = null;
  let mode = "safe";
  let bridgeError = "";
  let getCb = null;
  let postCb = null;
  let dialog = null;

  const log = function() {
    try {
      global.console.log.apply(global.console, arguments);
    } catch (_) {}
  };

  function fetchGet(url, cb, auth) {
    if (auth && token) {
      storage.fetch(url, "_mcp_get", "", "X-LibreSprite-Token", token);
    } else {
      storage.fetch(url, "_mcp_get");
    }
    getCb = function() {
      cb(storage.get("_mcp_get"), storage.get("_mcp_get_status"));
    };
  }

  function fetchPost(url, body, cb, auth) {
    const args = [
      url,
      "_mcp_post",
      "",
      "POST",
      body,
      "Content-Type",
      "application/json"
    ];
    if (auth && token) {
      args.push("X-LibreSprite-Token", token);
    }
    storage.fetch.apply(storage, args);
    postCb = function() {
      cb(storage.get("_mcp_post"), storage.get("_mcp_post_status"));
    };
  }

  function jsonGet(url, cb, auth) {
    fetchGet(url, function(text, status) {
      if (status !== 200) {
        cb(null, "HTTP " + status + " " + String(text || ""));
        return;
      }
      try {
        cb(JSON.parse(text), null);
      } catch (e) {
        cb(null, String(e));
      }
    }, auth);
  }

  function jsonPost(url, obj, cb, auth) {
    fetchPost(url, JSON.stringify(obj), function(text, status) {
      if (status < 200 || status >= 300) {
        cb(null, "HTTP " + status + " " + String(text || ""));
        return;
      }
      try {
        cb(JSON.parse(text), null);
      } catch (e) {
        cb(null, String(e));
      }
    }, auth);
  }

  function pair() {
    if (typeof storage === "undefined" || typeof storage.fetch !== "function") {
      bridgeError = "storage.fetch is unavailable in this LibreSprite build.";
      log("LibreSprite MCP:", bridgeError);
      paintUI();
      return;
    }

    jsonPost(
      BASE + "/pair",
      {
        bridge_version: BRIDGE_VERSION,
        libresprite_version: String(app.version || ""),
        platform: String(app.platform || ""),
        storage: typeof storage !== "undefined",
        storage_fetch: typeof storage.fetch === "function"
      },
      function(data, err) {
        if (err) {
          bridgeError = err;
          paintUI();
          return;
        }
        token = data.session_token;
        mode = data.mode || "safe";
        polling = true;
        bridgeError = "";
        paintUI();
        app.yield("poll");
      },
      false
    );
  }

  function authGetNext(cb) {
    jsonGet(BASE + "/next", cb, true);
  }

  function postResult(result) {
    jsonPost(BASE + "/result", result, function(_, err) {
      if (err) {
        bridgeError = err;
        polling = false;
        token = null;
        paintUI();
        return;
      }
      if (polling) {
        app.yield("poll", POLL_DELAY);
      }
    }, true);
  }

  function ok(requestId, result) {
    postResult({
      protocol_version: 1,
      request_id: requestId,
      ok: true,
      result: result || {}
    });
  }

  function fail(requestId, code, message, details) {
    postResult({
      protocol_version: 1,
      request_id: requestId,
      ok: false,
      error: {
        code: code,
        message: message,
        details: details || {}
      }
    });
  }

  function requireSprite() {
    if (!app.activeSprite) {
      throw new Error("NO_ACTIVE_SPRITE");
    }
    return app.activeSprite;
  }

  function layerAt(index) {
    const sprite = requireSprite();
    if (!Number.isInteger(index) || index < 0 || index >= sprite.layerCount) {
      throw new Error("LAYER_OUT_OF_RANGE");
    }
    const layer = sprite.layer(index);
    if (!layer || !layer.isImage) {
      throw new Error("LAYER_NOT_IMAGE");
    }
    return layer;
  }

  function celAt(layerIndex, frameIndex) {
    const layer = layerAt(layerIndex);
    const cel = layer.cel(frameIndex);
    if (!cel) {
      throw new Error("CEL_NOT_FOUND");
    }
    return cel;
  }

  function gotoFrame(frame) {
    if (!Number.isInteger(frame) || frame < 0) {
      throw new Error("FRAME_OUT_OF_RANGE");
    }
    app.command.clearParameters();
    app.command.setParameter("frame", String(frame + 1));
    app.command.GotoFrame();
    app.command.clearParameters();
    if (app.activeFrameNumber !== frame) {
      throw new Error("FRAME_OUT_OF_RANGE");
    }
  }

  function gotoLayer(layer) {
    const sprite = requireSprite();
    if (!Number.isInteger(layer) || layer < 0 || layer >= sprite.layerCount) {
      throw new Error("LAYER_OUT_OF_RANGE");
    }
    let guard = sprite.layerCount + 1;
    while (app.activeLayerNumber !== layer && guard-- > 0) {
      app.command.GotoNextLayer();
    }
    if (app.activeLayerNumber !== layer) {
      throw new Error("LAYER_SELECTION_FAILED");
    }
  }

  function countFrames() {
    requireSprite();
    const original = app.activeFrameNumber;
    app.command.GotoFirstFrame();
    const first = app.activeFrameNumber;
    let count = 1;
    let guard = 0;
    while (guard++ < 4096) {
      app.command.GotoNextFrame();
      if (app.activeFrameNumber === first) {
        break;
      }
      count++;
    }
    if (guard >= 4096) {
      throw new Error("FRAME_COUNT_GUARD_EXCEEDED");
    }
    gotoFrame(Math.min(original, count - 1));
    return count;
  }

  function packedRGBA(p) {
    const a = p.a === undefined ? 255 : p.a;
    return app.pixelColor.rgba(p.r | 0, p.g | 0, p.b | 0, a | 0);
  }

  function rgbaComponents(packed) {
    return {
      r: app.pixelColor.rgbaR(packed),
      g: app.pixelColor.rgbaG(packed),
      b: app.pixelColor.rgbaB(packed),
      a: app.pixelColor.rgbaA(packed)
    };
  }

  function decodePixel(raw) {
    const sprite = requireSprite();
    if (sprite.colorMode === ColorMode.INDEXED) {
      const palette = sprite.palette;
      if (raw < 0 || raw >= palette.length) {
        return { r: 0, g: 0, b: 0, a: 0, index: raw };
      }
      const c = rgbaComponents(palette.get(raw));
      c.index = raw;
      return c;
    }
    if (sprite.colorMode === ColorMode.GRAYSCALE) {
      const v = app.pixelColor.grayaV(raw);
      return { r: v, g: v, b: v, a: app.pixelColor.grayaA(raw) };
    }
    if (sprite.colorMode === ColorMode.BITMAP) {
      return { r: raw ? 255 : 0, g: raw ? 255 : 0, b: raw ? 255 : 0, a: 255 };
    }
    return rgbaComponents(raw);
  }

  function encodePixel(p) {
    const sprite = requireSprite();
    const a = p.a === undefined ? 255 : p.a;
    if (sprite.colorMode === ColorMode.INDEXED) {
      const palette = sprite.palette;
      for (let i = 0; i < palette.length; i++) {
        const c = rgbaComponents(palette.get(i));
        if (c.r === p.r && c.g === p.g && c.b === p.b && c.a === a) {
          return i;
        }
      }
      throw new Error("COLOR_NOT_IN_PALETTE");
    }
    if (sprite.colorMode === ColorMode.GRAYSCALE) {
      if (p.r !== p.g || p.g !== p.b) {
        throw new Error("GRAYSCALE_REQUIRES_EQUAL_RGB");
      }
      return app.pixelColor.graya(p.r | 0, a | 0);
    }
    if (sprite.colorMode === ColorMode.BITMAP) {
      if (!((p.r === 0 && p.g === 0 && p.b === 0) || (p.r === 255 && p.g === 255 && p.b === 255))) {
        throw new Error("BITMAP_REQUIRES_BLACK_OR_WHITE");
      }
      return p.r === 255 ? 1 : 0;
    }
    return packedRGBA(p);
  }

  function validateRGBA(p) {
    const vals = [p.r, p.g, p.b, p.a === undefined ? 255 : p.a];
    for (let i = 0; i < vals.length; i++) {
      if (!Number.isInteger(vals[i]) || vals[i] < 0 || vals[i] > 255) {
        throw new Error("INVALID_RGBA");
      }
    }
  }

  function bytesFromImage(img) {
    const raw = img.getImageData();
    const out = [];
    for (let i = 0; i < raw.length; i++) {
      out.push(raw[i]);
    }
    return out;
  }

  function drawPixels(p) {
    const cel = celAt(p.layer, p.frame);
    const img = cel.image;
    if (!Array.isArray(p.pixels) || p.pixels.length > MAX_PIXELS) {
      throw new Error("PIXEL_LIMIT");
    }

    const prepared = [];
    for (let i = 0; i < p.pixels.length; i++) {
      const q = p.pixels[i];
      if (!Number.isInteger(q.x) || !Number.isInteger(q.y)) {
        throw new Error("INVALID_COORDINATE");
      }
      if (q.x < 0 || q.y < 0 || q.x >= img.width || q.y >= img.height) {
        throw new Error("PIXEL_OUT_OF_RANGE");
      }
      validateRGBA(q);
      prepared.push({ x: q.x, y: q.y, value: encodePixel(q) });
    }

    for (let j = 0; j < prepared.length; j++) {
      const q = prepared[j];
      img.putPixel(q.x, q.y, q.value);
    }
    requireSprite().commit();
    return { written: prepared.length };
  }

  function pixelsRegion(p) {
    if (!Number.isInteger(p.width) || !Number.isInteger(p.height) || p.width <= 0 || p.height <= 0 || p.width * p.height > MAX_PIXELS) {
      throw new Error("INVALID_REGION");
    }
    const img = celAt(p.layer, p.frame).image;
    const pixels = [];
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        const x = p.x + xx;
        const y = p.y + yy;
        if (x < 0 || y < 0 || x >= img.width || y >= img.height) {
          pixels.push({ r: 0, g: 0, b: 0, a: 0 });
        } else {
          pixels.push(decodePixel(img.getPixel(x, y)));
        }
      }
    }
    return { x: p.x, y: p.y, width: p.width, height: p.height, pixels: pixels };
  }

  function setRegion(p) {
    if (!Number.isInteger(p.width) || !Number.isInteger(p.height) || p.width <= 0 || p.height <= 0 || p.width * p.height > MAX_PIXELS) {
      throw new Error("INVALID_REGION");
    }
    if (!Array.isArray(p.rgba) || p.rgba.length !== p.width * p.height * 4) {
      throw new Error("RGBA_LENGTH_MISMATCH");
    }
    const img = celAt(p.layer, p.frame).image;
    const prepared = [];
    let k = 0;
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        const q = { r: p.rgba[k++], g: p.rgba[k++], b: p.rgba[k++], a: p.rgba[k++] };
        validateRGBA(q);
        const x = p.x + xx;
        const y = p.y + yy;
        if (x < 0 || y < 0 || x >= img.width || y >= img.height) {
          throw new Error("REGION_OUT_OF_RANGE");
        }
        prepared.push({ x: x, y: y, value: encodePixel(q) });
      }
    }
    for (let i = 0; i < prepared.length; i++) {
      const q = prepared[i];
      img.putPixel(q.x, q.y, q.value);
    }
    requireSprite().commit();
    return { written: prepared.length };
  }

  function copyOrMoveRegion(p, move) {
    const img = celAt(p.layer, p.frame).image;
    if (p.width <= 0 || p.height <= 0 || p.width * p.height > MAX_PIXELS) {
      throw new Error("INVALID_REGION");
    }
    if (p.x < 0 || p.y < 0 || p.dest_x < 0 || p.dest_y < 0 || p.x + p.width > img.width || p.y + p.height > img.height || p.dest_x + p.width > img.width || p.dest_y + p.height > img.height) {
      throw new Error("REGION_OUT_OF_RANGE");
    }

    const tmp = [];
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        tmp.push(img.getPixel(p.x + xx, p.y + yy));
      }
    }

    if (move) {
      const clearValue = requireSprite().colorMode === ColorMode.INDEXED ? 0 : encodePixel({ r: 0, g: 0, b: 0, a: 0 });
      for (let yy = 0; yy < p.height; yy++) {
        for (let xx = 0; xx < p.width; xx++) {
          img.putPixel(p.x + xx, p.y + yy, clearValue);
        }
      }
    }

    let k = 0;
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        img.putPixel(p.dest_x + xx, p.dest_y + yy, tmp[k++]);
      }
    }
    requireSprite().commit();
    return { moved: !!move, pixels: tmp.length };
  }

  function clearRegion(p) {
    const img = celAt(p.layer, p.frame).image;
    if (p.width <= 0 || p.height <= 0 || p.width * p.height > MAX_PIXELS || p.x < 0 || p.y < 0 || p.x + p.width > img.width || p.y + p.height > img.height) {
      throw new Error("REGION_OUT_OF_RANGE");
    }
    const sprite = requireSprite();
    if (sprite.colorMode === ColorMode.INDEXED) {
      throw new Error("INDEXED_REGION_CLEAR_UNSUPPORTED");
    }
    const clearValue = encodePixel({ r: 0, g: 0, b: 0, a: 0 });
    let count = 0;
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        img.putPixel(p.x + xx, p.y + yy, clearValue);
        count++;
      }
    }
    sprite.commit();
    return { cleared: count };
  }

  function flipRegion(p) {
    const img = celAt(p.layer, p.frame).image;
    if (p.width <= 0 || p.height <= 0 || p.width * p.height > MAX_PIXELS || p.x < 0 || p.y < 0 || p.x + p.width > img.width || p.y + p.height > img.height) {
      throw new Error("REGION_OUT_OF_RANGE");
    }
    const tmp = [];
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        tmp.push(img.getPixel(p.x + xx, p.y + yy));
      }
    }
    for (let yy = 0; yy < p.height; yy++) {
      for (let xx = 0; xx < p.width; xx++) {
        const sx = p.horizontal ? p.width - 1 - xx : xx;
        const sy = p.horizontal ? yy : p.height - 1 - yy;
        img.putPixel(p.x + xx, p.y + yy, tmp[sy * p.width + sx]);
      }
    }
    requireSprite().commit();
    return { flipped: true, horizontal: !!p.horizontal };
  }

  function resolvedRGBAImage(img) {
    const out = [];
    for (let y = 0; y < img.height; y++) {
      for (let x = 0; x < img.width; x++) {
        const c = decodePixel(img.getPixel(x, y));
        out.push(c.r, c.g, c.b, c.a);
      }
    }
    return out;
  }

  function previewFrame(frame) {
    const sprite = requireSprite();
    const layers = [];
    for (let i = 0; i < sprite.layerCount; i++) {
      const layer = sprite.layer(i);
      if (!layer || !layer.isImage || !layer.isVisible) {
        continue;
      }
      const cel = layer.cel(frame);
      if (!cel || !cel.image) {
        continue;
      }
      const entry = { layer: i, x: cel.x, y: cel.y, width: cel.image.width, height: cel.image.height };
      if (sprite.colorMode === ColorMode.RGB) {
        entry.png_data_uri = cel.image.getPNGData();
      } else {
        entry.rgba = resolvedRGBAImage(cel.image);
      }
      layers.push(entry);
    }
    return {
      frame: frame,
      canvas_width: sprite.width,
      canvas_height: sprite.height,
      color_mode: sprite.colorMode,
      layers: layers
    };
  }

  function health() {
    return {
      connected: true,
      bridge_version: BRIDGE_VERSION,
      libresprite_version: String(app.version || ""),
      platform: String(app.platform || ""),
      mode: mode,
      capabilities: {
        frame_duration_read: false,
        frame_duration_write: false,
        frame_tags: false,
        export_spritesheet: false,
        preview: true,
        pixels: true,
        layers: true,
        cels: true,
        frames: true,
        arbitrary_script: mode === "dev"
      },
      limitations: {
        frame_duration: "Upstream scripting exposes FrameProperties as UI-only and no direct frame-duration API.",
        frame_tags: "Current scripting reference exposes commands but no verified non-interactive tag object API.",
        export_spritesheet: "ExportSpriteSheet is exposed as a GUI command without a verified non-interactive parameter API.",
        indexed_region_clear: "Transparent palette index is not exposed reliably; SAFE mode refuses destructive guessing."
      }
    };
  }

  function unsupported(code, message) {
    return { ok: false, unsupported: true, code: code, message: message };
  }

  function dispatch(op, p) {
    switch (op) {
      case "health":
        return health();

      case "get_sprite_info": {
        const sprite = requireSprite();
        return {
          width: sprite.width,
          height: sprite.height,
          filename: sprite.filename,
          color_mode: sprite.colorMode,
          layer_count: sprite.layerCount,
          active_frame: app.activeFrameNumber,
          active_layer: app.activeLayerNumber,
          frame_count: countFrames()
        };
      }

      case "get_active_frame":
        return { frame: app.activeFrameNumber };

      case "set_active_frame":
        gotoFrame(p.frame);
        return { frame: app.activeFrameNumber };

      case "get_frames": {
        const count = countFrames();
        const frames = [];
        for (let i = 0; i < count; i++) frames.push(i);
        return { count: count, frames: frames };
      }

      case "get_layers": {
        const sprite = requireSprite();
        const layers = [];
        for (let i = 0; i < sprite.layerCount; i++) {
          const layer = sprite.layer(i);
          layers.push({
            index: i,
            name: layer.name,
            visible: layer.isVisible,
            editable: layer.isEditable,
            image: layer.isImage,
            background: layer.isBackground,
            cel_count: layer.celCount
          });
        }
        return { layers: layers };
      }

      case "add_frame": {
        const before = countFrames();
        app.command.clearParameters();
        app.command.setParameter("content", "empty");
        app.command.NewFrame();
        app.command.clearParameters();
        const after = countFrames();
        if (after !== before + 1) throw new Error("POSTCONDITION_FAILED");
        return { before: before, after: after, frame: app.activeFrameNumber };
      }

      case "insert_frame": {
        gotoFrame(p.frame);
        const before = countFrames();
        gotoFrame(p.frame);
        app.command.clearParameters();
        app.command.setParameter("content", "empty");
        app.command.NewFrame();
        app.command.clearParameters();
        const after = countFrames();
        if (after !== before + 1) throw new Error("POSTCONDITION_FAILED");
        return { before: before, after: after, frame: app.activeFrameNumber };
      }

      case "duplicate_frame": {
        gotoFrame(p.frame);
        const before = countFrames();
        gotoFrame(p.frame);
        app.command.clearParameters();
        app.command.setParameter("content", "frame");
        app.command.NewFrame();
        app.command.clearParameters();
        const after = countFrames();
        if (after !== before + 1) throw new Error("POSTCONDITION_FAILED");
        return { before: before, after: after, frame: app.activeFrameNumber };
      }

      case "delete_frame": {
        gotoFrame(p.frame);
        const before = countFrames();
        if (before <= 1) throw new Error("CANNOT_DELETE_ONLY_FRAME");
        gotoFrame(p.frame);
        app.command.RemoveFrame();
        const after = countFrames();
        if (after !== before - 1) throw new Error("POSTCONDITION_FAILED");
        return { before: before, after: after, frame: app.activeFrameNumber };
      }

      case "get_frame_durations":
      case "set_frame_duration":
      case "set_frame_durations":
        return unsupported("UNSUPPORTED_FRAME_DURATION", health().limitations.frame_duration);

      case "create_layer": {
        const sprite = requireSprite();
        const before = sprite.layerCount;
        app.command.clearParameters();
        app.command.setParameter("name", String(p.name || "Layer"));
        app.command.setParameter("top", "true");
        app.command.NewLayer();
        app.command.clearParameters();
        const after = sprite.layerCount;
        if (after !== before + 1) throw new Error("POSTCONDITION_FAILED");
        const layer = sprite.layer(after - 1);
        if (!layer || layer.name !== String(p.name || "Layer")) throw new Error("POSTCONDITION_FAILED");
        return { layer: after - 1, layer_count: after, name: layer.name };
      }

      case "delete_layer": {
        const sprite = requireSprite();
        const before = sprite.layerCount;
        if (before <= 1) throw new Error("CANNOT_DELETE_ONLY_LAYER");
        gotoLayer(p.layer);
        app.command.RemoveLayer();
        const after = sprite.layerCount;
        if (after !== before - 1) throw new Error("POSTCONDITION_FAILED");
        return { layer_count: after };
      }

      case "rename_layer": {
        const layer = layerAt(p.layer);
        layer.name = String(p.name);
        requireSprite().commit();
        if (layer.name !== String(p.name)) throw new Error("POSTCONDITION_FAILED");
        return { layer: p.layer, name: layer.name };
      }

      case "set_layer_visibility": {
        const layer = layerAt(p.layer);
        layer.isVisible = !!p.visible;
        requireSprite().commit();
        return { layer: p.layer, visible: layer.isVisible };
      }

      case "set_active_layer":
        gotoLayer(p.layer);
        return { layer: app.activeLayerNumber };

      case "get_cel": {
        const cel = celAt(p.layer, p.frame);
        return {
          layer: p.layer,
          frame: p.frame,
          x: cel.x,
          y: cel.y,
          width: cel.image.width,
          height: cel.image.height,
          stride: cel.image.stride,
          format: cel.image.format
        };
      }

      case "copy_cel": {
        const source = celAt(p.source_layer, p.source_frame);
        const target = celAt(p.target_layer, p.target_frame);
        if (source.image.width !== target.image.width || source.image.height !== target.image.height || source.image.stride !== target.image.stride) {
          throw new Error("CEL_IMAGE_SIZE_MISMATCH");
        }
        const sourceData = source.image.getImageData();
        const cloned = new Uint8Array(sourceData.length);
        for (let i = 0; i < sourceData.length; i++) cloned[i] = sourceData[i];
        target.image.putImageData(cloned);
        target.setPosition(source.x, source.y);
        requireSprite().commit();
        return { copied: true, target_layer: p.target_layer, target_frame: p.target_frame };
      }

      case "move_cel": {
        const cel = celAt(p.layer, p.frame);
        cel.setPosition(p.x, p.y);
        requireSprite().commit();
        if (cel.x !== p.x || cel.y !== p.y) throw new Error("POSTCONDITION_FAILED");
        return { x: cel.x, y: cel.y };
      }

      case "clear_cel": {
        gotoFrame(p.frame);
        gotoLayer(p.layer);
        app.command.ClearCel();
        const remaining = layerAt(p.layer).cel(p.frame);
        if (remaining) throw new Error("POSTCONDITION_FAILED");
        return { cleared: true };
      }

      case "get_pixel": {
        const img = celAt(p.layer, p.frame).image;
        if (!Number.isInteger(p.x) || !Number.isInteger(p.y) || p.x < 0 || p.y < 0 || p.x >= img.width || p.y >= img.height) {
          throw new Error("PIXEL_OUT_OF_RANGE");
        }
        const result = decodePixel(img.getPixel(p.x, p.y));
        result.x = p.x;
        result.y = p.y;
        return result;
      }

      case "get_pixels":
      case "get_region":
        return pixelsRegion(p);

      case "draw_pixels":
        return drawPixels(p);

      case "set_region":
        return setRegion(p);

      case "copy_region":
        return copyOrMoveRegion(p, false);

      case "move_region":
        return copyOrMoveRegion(p, true);

      case "clear_region":
        return clearRegion(p);

      case "flip_region":
        return flipRegion(p);

      case "get_image_data": {
        const img = celAt(p.layer, p.frame).image;
        return {
          width: img.width,
          height: img.height,
          stride: img.stride,
          format: img.format,
          data: bytesFromImage(img)
        };
      }

      case "set_image_data": {
        const img = celAt(p.layer, p.frame).image;
        if (!Array.isArray(p.data) || p.data.length !== img.stride * img.height) {
          throw new Error("IMAGE_DATA_LENGTH_MISMATCH");
        }
        const arr = new Uint8Array(p.data.length);
        for (let i = 0; i < p.data.length; i++) {
          const value = p.data[i];
          if (!Number.isInteger(value) || value < 0 || value > 255) throw new Error("INVALID_IMAGE_BYTE");
          arr[i] = value;
        }
        img.putImageData(arr);
        requireSprite().commit();
        return { bytes: p.data.length };
      }

      case "get_palette": {
        const palette = requireSprite().palette;
        const colors = [];
        for (let i = 0; i < palette.length; i++) {
          const c = rgbaComponents(palette.get(i));
          c.index = i;
          colors.push(c);
        }
        return { colors: colors };
      }

      case "set_palette_color": {
        const palette = requireSprite().palette;
        if (!Number.isInteger(p.index) || p.index < 0 || p.index >= palette.length) {
          throw new Error("PALETTE_INDEX_OUT_OF_RANGE");
        }
        validateRGBA(p);
        palette.set(p.index, packedRGBA(p));
        requireSprite().commit();
        const c = rgbaComponents(palette.get(p.index));
        c.index = p.index;
        return c;
      }

      case "get_tags":
      case "create_tag":
      case "delete_tag":
        return unsupported("UNSUPPORTED_FRAME_TAGS", health().limitations.frame_tags);

      case "render_frame_preview":
        gotoFrame(p.frame);
        return previewFrame(p.frame);

      case "save_sprite":
        requireSprite().save();
        return { saved: true, filename: requireSprite().filename };

      case "save_as":
        requireSprite().saveAs(String(p.path), false);
        return { saved: true, filename: requireSprite().filename, path: String(p.path) };

      case "open_sprite":
        app.open(String(p.path));
        return { opened: true, path: String(p.path), filename: requireSprite().filename };

      case "export_png":
        if (p.frame !== null && p.frame !== undefined) gotoFrame(p.frame);
        requireSprite().saveAs(String(p.path), true);
        return { exported: true, path: String(p.path), format: "png" };

      case "export_gif":
        requireSprite().saveAs(String(p.path), true);
        return { exported: true, path: String(p.path), format: "gif" };

      case "export_spritesheet":
        return unsupported("UNSUPPORTED_SPRITESHEET_EXPORT", health().limitations.export_spritesheet);

      case "run_script":
        if (mode !== "dev") throw new Error("DEV_MODE_REQUIRED");
        return {
          output: new Function("return (function(){" + String(p.script) + "\n}).call(this);")()
        };

      default:
        throw new Error("OPERATION_NOT_ALLOWED");
    }
  }

  function execute(req) {
    const rid = req.request_id;
    const op = req.operation;
    const payload = req.payload || {};
    try {
      if (op === "run_script" && mode !== "dev") {
        fail(rid, "DEV_MODE_REQUIRED", "run_script is unavailable in SAFE mode");
        return;
      }
      ok(rid, dispatch(op, payload));
    } catch (e) {
      const message = String(e.message || e);
      fail(rid, message.replace(/\s+/g, "_").toUpperCase(), message);
    }
  }

  function poll() {
    if (!polling || !token) return;
    authGetNext(function(data, err) {
      if (err) {
        bridgeError = err;
        polling = false;
        token = null;
        paintUI();
        return;
      }
      if (data && data.idle) {
        app.yield("poll", POLL_DELAY);
        return;
      }
      if (data && data.request_id && data.operation) {
        execute(data);
        return;
      }
      app.yield("poll", POLL_DELAY);
    });
  }

  function paintUI() {
    if (dialog) dialog.close();
    dialog = app.createDialog();
    dialog.title = "LibreSprite MCP (" + mode.toUpperCase() + ")";
    if (bridgeError) {
      dialog.addLabel("Error: " + bridgeError);
    } else if (polling) {
      dialog.addLabel("Connected - " + mode.toUpperCase() + " mode");
    } else {
      dialog.addLabel("Disconnected");
    }
    dialog.addBreak();
    if (!polling) dialog.addButton("Connect", "connect");
    else dialog.addButton("Disconnect", "disconnect");
  }

  function onEvent(event) {
    switch (event) {
      case "init":
        active = true;
        if (typeof storage === "undefined" || typeof storage.fetch !== "function") {
          bridgeError = "storage.fetch is unavailable in this LibreSprite build.";
        }
        paintUI();
        return;
      case "_close":
        active = false;
        polling = false;
        token = null;
        return;
      case "connect_click":
        pair();
        return;
      case "disconnect_click":
        polling = false;
        token = null;
        paintUI();
        return;
      case "_mcp_get_fetch":
        if (getCb) {
          const fn = getCb;
          getCb = null;
          fn();
        }
        return;
      case "_mcp_post_fetch":
        if (postCb) {
          const fn = postCb;
          postCb = null;
          fn();
        }
        return;
      case "poll":
        if (active) poll();
        return;
      default:
        return;
    }
  }

  global.onEvent = onEvent;
})();

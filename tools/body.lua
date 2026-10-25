-- Turns the body of a writer's Word file into page text for the site.
-- Drops the template's title, notes and details table; maps the Word
-- styles (Overview, Closing Note) to the
-- site's boxes; marks chapter subheadings and verse groups.

local kind = "article"

local function style_of(b)
  if b.t == "Div" and b.attributes and b.attributes["custom-style"] then
    return b.attributes["custom-style"]
  end
  return nil
end

local function plain_blocks(b)
  -- the paragraphs inside a styled Div
  if b.t == "Div" then return b.content end
  return { b }
end

function Pandoc(doc)
  if doc.meta.kind then kind = pandoc.utils.stringify(doc.meta.kind) end
  local out, scripture = {}, nil
  local seen_table = false

  local function flush()
    if scripture then
      out[#out + 1] = pandoc.Div(scripture, pandoc.Attr("", { "scripture" }))
      scripture = nil
    end
  end

  local function emit(b)
    out[#out + 1] = b
  end

  for _, b in ipairs(doc.blocks) do
    if not seen_table then
      if b.t == "Table" then seen_table = true end
    else
      local st = style_of(b)
      if st == "Writer Note" then
        -- dropped
      elseif st == "Scripture" or st == "Scripture Reference" then
        -- scripture text is never published; refer to it by chapter and verse
      elseif st == "Overview" then
        flush()
        local paras = {}
        for i, p in ipairs(plain_blocks(b)) do
          if i == 1 and (p.t == "Para" or p.t == "Plain") then
            local c = { pandoc.Strong({ pandoc.Str("Overview.") }), pandoc.Space() }
            for _, x in ipairs(p.content) do c[#c + 1] = x end
            paras[#paras + 1] = pandoc.Para(c)
          else
            paras[#paras + 1] = p
          end
        end
        emit(pandoc.Div(paras, pandoc.Attr("", { "overview" })))
      elseif st == "Closing Note" then
        flush()
        emit(pandoc.Div(plain_blocks(b), pandoc.Attr("", { "cta" })))
      else
        flush()
        if b.t == "Header" then
          local level = b.level
          if level < 2 then level = 2 end
          local classes = {}
          if kind == "chapter" then
            if level == 2 then classes = { "main-sub" } end
            if level == 3 then classes = { "vgroup" } end
          end
          emit(pandoc.Header(level, b.content, pandoc.Attr("", classes)))
        elseif b.t == "Div" and st then
          -- any other styled paragraph: keep the text, drop the style
          for _, p in ipairs(b.content) do emit(p) end
        else
          emit(b)
        end
      end
    end
  end
  flush()
  return pandoc.Pandoc(out, doc.meta)
end

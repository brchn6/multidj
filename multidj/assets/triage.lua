-- multidj-triage: numpad key bindings for fast track audition.
-- Loaded only when mpv is launched via `multidj triage` (--script= flag).
-- Never installed to ~/.config/mpv/scripts/ to avoid hijacking normal mpv sessions.

local utils = require("mp.utils")

local function tag_and_next(rating, hard_delete)
    local path = mp.get_property("path")
    if not path then return end

    local args = {
        "multidj", "triage", "tag",
        "--path", path,
        "--rating", tostring(rating),
    }
    if hard_delete then
        table.insert(args, "--hard-delete")
    end
    utils.subprocess({args = args, playback_only = false})

    if rating == 0 and hard_delete then
        mp.osd_message("DELETED from disk", 2)
    elseif rating == 0 then
        mp.osd_message("Trashed", 1.5)
    else
        local filled = string.rep("*", rating)
        local empty  = string.rep("-", 5 - rating)
        mp.osd_message(filled .. empty .. "  (" .. rating .. "/5)", 1.5)
    end

    mp.commandv("playlist-next", "force")
end

mp.add_key_binding("KP0",       "triage-trash",       function() tag_and_next(0, false) end)
mp.add_key_binding("Shift+KP0", "triage-hard-delete", function() tag_and_next(0, true)  end)
mp.add_key_binding("KP1", "triage-rate-1", function() tag_and_next(1, false) end)
mp.add_key_binding("KP2", "triage-rate-2", function() tag_and_next(2, false) end)
mp.add_key_binding("KP3", "triage-rate-3", function() tag_and_next(3, false) end)
mp.add_key_binding("KP4", "triage-rate-4", function() tag_and_next(4, false) end)
mp.add_key_binding("KP5", "triage-rate-5", function() tag_and_next(5, false) end)

mp.add_key_binding("n", "triage-skip", function()
    mp.osd_message("Skipped", 1)
    mp.commandv("playlist-next", "force")
end)

-- Override default +-5s seek with +-30s
mp.add_key_binding("RIGHT", "seek-fwd-30", function() mp.commandv("seek", "30") end)
mp.add_key_binding("LEFT",  "seek-bck-30", function() mp.commandv("seek", "-30") end)

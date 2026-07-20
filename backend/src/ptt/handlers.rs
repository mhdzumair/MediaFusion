use fancy_regex::Regex;
/// Mirrors add_defaults() from PTT/handlers.py
///
/// Handlers are added in the exact same order as the Python source so that
/// the "first match wins" / skipIfAlreadyFound semantics are preserved.
use once_cell::sync::OnceCell;

use super::engine::{Ctx, FieldValue, HandlerReturn, MatchInfo, Opts, Parser, compile, compile_i};
use super::transformers::{
    arc, array, tr_boolean, tr_first_integer, tr_integer, tr_lowercase, tr_none, tr_range_func,
    tr_range_x_of_y, tr_transform_resolution, tr_uppercase, uniq_concat, value,
};

fn re(pattern: &str) -> Regex {
    compile(pattern)
}
fn rei(pattern: &str) -> Regex {
    compile_i(pattern)
}

pub fn add_defaults(p: &mut Parser) {
    // ── Year pre-check ────────────────────────────────────────────────────
    p.add(
        "year",
        re(r"\b19\d{2}\s?-\s?20\d{2}\b"),
        arc(tr_first_integer),
        Opts::defaults().with_remove(false),
    );

    // ── Pre-hardcoded cleanup ─────────────────────────────────────────────
    p.add(
        "title",
        rei(r"360.Degrees.of.Vision.The.Byakugan'?s.Blind.Spot"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "title",
        rei(r"\b100[ .-]*years?[ .-]*quest\b"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "title",
        rei(r"\[?(\+.)?Extras\]?"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "title",
        rei(r"(\+Movies)?\+Specials"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "group",
        re(r"-?EDGE2020"),
        value("EDGE2020"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "title",
        rei(r"TV Money"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );

    // ── Container ─────────────────────────────────────────────────────────
    p.add(
        "container",
        rei(r"\.?[\[(]?\b(MKV|AVI|MP4|WMV|MPG|MPEG)\b[\u{005D})]?"),
        arc(tr_lowercase),
        Opts::defaults(),
    );

    // ── Torrent extension ─────────────────────────────────────────────────
    p.add(
        "torrent",
        re(r"\.torrent$"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Adult ─────────────────────────────────────────────────────────────
    p.add(
        "adult",
        re(r"\b(XXX|xxx|Xxx)\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Scene ─────────────────────────────────────────────────────────────
    p.add("scene",
        re(r"(?i)^(?=.*(\b\d{3,4}p\b).*([_. ]WEB[_. ])(?!DL)\b)|\b(-CAKES|-GGEZ|-GGWP|-GLHF|-GOSSIP|-NAISU|-KOGI|-PECULATE|-SLOT|-EDITH|-ETHEL|-ELEANOR|-B2B|-SPAMnEGGS|-FTP|-DiRT|-SYNCOPY|-BAE|-SuccessfulCrab|-NHTFS|-SURCODE|-B0MBARDIERS)"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false));

    // ── Extras ────────────────────────────────────────────────────────────
    p.add(
        "extras",
        re(r"\bNCED\b"),
        uniq_concat(value("NCED")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        re(r"\bNCOP\b"),
        uniq_concat(value("NCOP")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        re(r"\bNC\b"),
        uniq_concat(value("NC")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        rei(r"\bOVA\b"),
        uniq_concat(value("OVA")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        rei(r"\bED(\d?v?\d?)\b"),
        uniq_concat(value("ED")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        re(r"\bOPv?(\d+)?\b"),
        uniq_concat(value("OP")),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "extras",
        rei(r"\bDeleted.*Scenes?\b"),
        uniq_concat(value("Deleted Scene")),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "extras",
        rei(r"\bFeaturettes?\b(?!.*\b(?:19\d{2}|20\d{2})\b)"),
        uniq_concat(value("Featurette")),
        Opts::defaults()
            .with_skip_from_title(true)
            .with_remove(false),
    );
    p.add(
        "extras",
        rei(r"\b(?:Sample)\b(?!.*\b(?:19\d{2}|20\d{2})\b)"),
        uniq_concat(value("Sample")),
        Opts::defaults()
            .with_skip_from_title(true)
            .with_remove(false),
    );
    p.add(
        "extras",
        rei(r"\bTrailers?\b(?!.*\b(?:19\d{2}|20\d{2}|.(Park|And))\b)"),
        uniq_concat(value("Trailer")),
        Opts::defaults()
            .with_skip_from_title(true)
            .with_remove(false),
    );

    // ── PPV ───────────────────────────────────────────────────────────────
    p.add(
        "ppv",
        rei(r"\bPPV\b"),
        arc(tr_boolean),
        Opts::defaults()
            .with_skip_from_title(true)
            .with_remove(true),
    );
    p.add(
        "ppv",
        rei(r"\b\W?Fight.?Nights?\W?\b"),
        arc(tr_boolean),
        Opts::defaults()
            .with_skip_from_title(true)
            .with_remove(false),
    );

    // ── Site (before languages to strip domain country codes) ─────────────
    p.add(
        "site",
        rei(r"^(www?[., ][\w-]+[. ][\w-]+(?:[. ][\w-]+)?)\s+-\s*"),
        arc(tr_none),
        Opts {
            skip_if_already_found: false,
            skip_from_title: true,
            remove: true,
            skip_if_first: false,
        },
    );
    p.add(
        "site",
        rei(r"\bwww.+rodeo\b"),
        arc(tr_lowercase),
        Opts::defaults().with_remove(true),
    );

    // ── Resolution ────────────────────────────────────────────────────────
    p.add(
        "resolution",
        rei(r"\[?\]?3840x\d{4}[\u{005D})?]?"),
        value("2160p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"\[?\]?1920x\d{3,4}[\u{005D})?]?"),
        value("1080p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"\[?\]?1280x\d{3}[\u{005D})?]?"),
        value("720p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"\[?\]?(\d{3,4}x\d{3,4})[\u{005D})?]?p?"),
        value("$1p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(480|720|1080)0[pi]"),
        value("$1p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:QHD|QuadHD|WQHD|2560(\d+)?x(\d+)?1440p?)"),
        value("1440p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:Full HD|FHD|1920(\d+)?x(\d+)?1080p?)"),
        value("1080p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:BD|HD|M)(2160p?|4k)"),
        value("2160p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:BD|HD|M)1080p?"),
        value("1080p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:BD|HD|M)720p?"),
        value("720p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(?:BD|HD|M)480p?"),
        value("480p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"\b(?:4k|2160p|1080p|720p|480p)(?!.*\b(?:4k|2160p|1080p|720p|480p)\b)"),
        arc(tr_transform_resolution),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"\b4k|21600?[pi]\b"),
        value("2160p"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(\d{3,4}[pi])"),
        arc(tr_lowercase),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "resolution",
        rei(r"(240|360|480|576|720|1080|2160|3840)[pi]"),
        arc(tr_lowercase),
        Opts::defaults().with_remove(true),
    );

    // ── Episode code ──────────────────────────────────────────────────────
    p.add(
        "episode_code",
        re(r"[\[\(]([A-Fa-f0-9]{8})[\u{005D}\)]"),
        arc(tr_uppercase),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "episode_code",
        re(r"[\[\(]([0-9]{8})[\u{005D}\)]"),
        arc(tr_uppercase),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );

    // ── Trash ─────────────────────────────────────────────────────────────
    p.add(
        "trash",
        rei(r"\b(?:H[DQ][ .-]*)?CAM(?!.?(S|E|\()\d+)(?:H[DQ])?(?:[ .-]*Rip|Rp)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\b(?:H[DQ][ .-]*)?S[ \.\-]print\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\b(?:HD[ .-]*)?T(?:ELE)?(C|S)(?:INE|YNC)?(?:Rip)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\bPre.?DVD(?:Rip)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\b(?:DVD?|BD|BR|HD)?[ .-]*Scr(?:eener)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\bDVB[ .-]*(?:Rip)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\bSAT[ .-]*Rips?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\bLeaked\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "trash",
        rei(r"threesixtyp"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        re(r"\bR5|R6\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(false),
    );
    p.add(
        "trash",
        rei(r"\bDeleted.*Scenes?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "trash",
        rei(r"\bHQ.?(Clean)?.?(Aud(io)?)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Date ──────────────────────────────────────────────────────────────
    // (We store as string "YYYY-MM-DD"; arrow-equivalent parsing via chrono)
    p.add("date",
        re(r"(?:\W|^)([\[(]?(?:19[6-9]|20[012])[0-9]([. \-/\\])(?:0[1-9]|1[012])\2(?:0[1-9]|[12][0-9]|3[01])[\u{005D})]?)(?:\W|$)"),
        arc(tr_none), Opts::defaults().with_remove(true));

    // ── Complete ──────────────────────────────────────────────────────────
    p.add(
        "complete",
        re(r"\b((?:19\d|20[012])\d[ .]?-[ .]?(?:19\d|20[012])\d)\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        re(r"[(\[][ .]?((?:19\d|20[012])\d[ .]?-[ .]?\d{2})[ .]?[)\u{005D}]"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Bit rate ──────────────────────────────────────────────────────────
    p.add(
        "bitrate",
        rei(r"\b\d+[kmg]bps\b"),
        arc(tr_lowercase),
        Opts::defaults().with_remove(true),
    );

    // ── Year ──────────────────────────────────────────────────────────────
    p.add(
        "year",
        re(r"\b(20[0-9]{2}|2100)(?!\D*\d{4}\b)"),
        arc(tr_integer),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "year",
        re(r"[^SE][(\[]?(?!^)(?<!\d)((?:19\d|20[012])\d)(?!\d|kbps)[)\u{005D}]?"),
        arc(tr_integer),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "year",
        rei(r"(?!^\w{4})^[(\[]?((?:19\d|20[012])\d)(?!\d|kbps)[)\u{005D}]?"),
        arc(tr_integer),
        Opts::defaults().with_remove(true),
    );

    // ── Edition ───────────────────────────────────────────────────────────
    p.add(
        "edition",
        rei(r"\b\d{2,3}(th)?[\.\s\-\+_\/(),]Anniversary[\.\s\-\+_\/(),](Edition|Ed)?\b"),
        value("Anniversary Edition"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bUltimate[\.\s\-\+_\/(),]Edition\b"),
        value("Ultimate Edition"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bExtended[\.\s\-\+_\/(),]Director(\')?s\b"),
        value("Directors Cut"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\b(custom.?)?Extended\b"),
        value("Extended Edition"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bDirector(\')?s.?Cut\b"),
        value("Directors Cut"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bCollector(\')?s\b"),
        value("Collectors Edition"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bTheatrical\b"),
        value("Theatrical"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\buncut(?!.gems)\b"),
        value("Uncut"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bIMAX\b"),
        value("IMAX"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\b\.Diamond\.\b"),
        value("Diamond Edition"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "edition",
        rei(r"\bRemaster(?:ed)?\b"),
        value("Remastered"),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );

    // ── Upscaled ──────────────────────────────────────────────────────────
    p.add(
        "upscaled",
        rei(r"\b(?:AI.?)?(Upscal(ed?|ing)|Enhanced?)\b"),
        arc(tr_boolean),
        Opts::defaults(),
    );
    p.add(
        "upscaled",
        rei(r"\b(?:iris2|regrade|ups(uhd|fhd|hd|4k))\b"),
        arc(tr_boolean),
        Opts::defaults(),
    );
    p.add(
        "upscaled",
        rei(r"\b\.AI\.\b"),
        arc(tr_boolean),
        Opts::defaults(),
    );

    // ── Various flags ─────────────────────────────────────────────────────
    p.add(
        "convert",
        re(r"\bCONVERT\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "hardcoded",
        rei(r"\b(HC|HARDCODED)\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "proper",
        rei(r"\b(?:REAL.)?PROPER\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "repack",
        rei(r"\bREPACK|RERIP\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "retail",
        rei(r"\bRetail\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "remastered",
        rei(r"\bRemaster(?:ed)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "documentary",
        rei(r"\bDOCU(?:menta?ry)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_skip_from_title(true),
    );
    p.add(
        "unrated",
        rei(r"\bunrated\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "uncensored",
        rei(r"\buncensored\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "commentary",
        rei(r"\bcommentary\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Region ────────────────────────────────────────────────────────────
    p.add(
        "region",
        re(r"R\dJ?\b"),
        arc(tr_uppercase),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "region",
        rei(r"\b(PAL|NTSC|SECAM)\b"),
        arc(tr_uppercase),
        Opts::defaults().with_remove(true),
    );

    // ── Quality ───────────────────────────────────────────────────────────
    p.add(
        "quality",
        rei(r"\b(?:HD[ .-]*)?T(?:ELE)?S(?:YNC)?(?:Rip)?\b"),
        value("TeleSync"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        re(r"\b(?:HD[ .-]*)?T(?:ELE)?C(?:INE)?(?:Rip)?\b"),
        value("TeleCine"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:DVD?|BD|BR|HD)?[ .-]*Scr(?:eener)?\b"),
        value("SCR"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bP(?:RE)?-?(HD|DVD)(?:Rip)?\b"),
        value("SCR"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bBlu[ .-]*Ray\b(?=.*remux)"),
        value("BluRay REMUX"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"(?:BD|BR|UHD)[- ]?remux"),
        value("BluRay REMUX"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"remux[. -]*\bBlu[ .-]*Ray\b"),
        value("BluRay REMUX"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bremux\b"),
        value("REMUX"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bBlu[ .-]*Ray\b(?![ .-]*Rip)"),
        value("BluRay"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bUHD[ .-]*Rip\b"),
        value("UHDRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bHD[ .-]*Rip\b"),
        value("HDRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bMicro[ .-]*HD\b"),
        value("HDRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:BR|Blu[ .-]*Ray)[ .-]*Rip\b"),
        value("BRRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bBD[ .-]*Rip\b|\bBDR\b|\bBD-RM\b|(?:\[|\()BD(?:\]|\)|[ .,-])"),
        value("BDRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:HD[ .-]*)?DVD[ .-]*Rip\b"),
        value("DVDRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bVHS[ .-]*Rip?\b"),
        value("VHSRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bDVD(?:R\d?|.*Mux)?\b"),
        value("DVD"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bVHS\b"),
        value("VHS"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bPPVRip\b"),
        value("PPVRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bHD.?TV.?Rip\b"),
        value("HDTVRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bDVB[ .-]*(?:Rip)?\b"),
        value("HDTV"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bSAT[ .-]*Rips?\b"),
        value("SATRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bTVRips?\b"),
        value("TVRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bR5\b"),
        value("R5"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:DL|WEB|BD|BR)MUX\b"),
        value("WEBMux"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bWEB[ .-]*Rip\b"),
        value("WEBRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bWEB[ .-]?DL[ .-]?Rip\b"),
        value("WEB-DLRip"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bWEB[ .-]*(DL|.BDrip|.DLRIP)\b"),
        value("WEB-DL"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\b(?<!\w.)WEB\b|\bWEB(?!([ \.\-\(\u{005D},]+\d))\b"),
        value("WEB"),
        Opts::defaults()
            .with_remove(true)
            .with_skip_from_title(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:H[DQ][ .-]*)?CAM(?!.?(S|E|\()\d+)(?:H[DQ])?(?:[ .-]*Rip|Rp)?\b"),
        value("CAM"),
        Opts::defaults()
            .with_remove(true)
            .with_skip_from_title(true),
    );
    p.add(
        "quality",
        rei(r"\b(?:H[DQ][ .-]*)?S[ \.\-]print"),
        value("CAM"),
        Opts::defaults()
            .with_remove(true)
            .with_skip_from_title(true),
    );
    p.add(
        "quality",
        rei(r"\bPDTV\b"),
        value("PDTV"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "quality",
        rei(r"\bHD(.?TV)?\b"),
        value("HDTV"),
        Opts::defaults().with_remove(true),
    );

    // ── Bit depth ─────────────────────────────────────────────────────────
    p.add(
        "bit_depth",
        rei(r"\bhevc\s?10\b"),
        value("10bit"),
        Opts::defaults(),
    );
    p.add(
        "bit_depth",
        rei(r"(?:8|10|12)[-\.]?(?=bit\b)"),
        value("$1bit"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "bit_depth",
        rei(r"\bhdr10\b"),
        value("10bit"),
        Opts::defaults(),
    );
    p.add(
        "bit_depth",
        rei(r"\bhi10\b"),
        value("10bit"),
        Opts::defaults(),
    );
    // Cleanup: strip spaces/hyphens from bit_depth value
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        if let Some(FieldValue::Str(v)) = ctx.result.get_mut("bit_depth") {
            *v = v.replace([' ', '-'], "");
        }
        None
    }));

    // ── HDR ───────────────────────────────────────────────────────────────
    p.add(
        "hdr",
        rei(r"\bDV\b|dolby.?vision|\bDoVi\b"),
        uniq_concat(value("DV")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "hdr",
        rei(r"HDR10(?:\+|[-\.\s]?plus)"),
        uniq_concat(value("HDR10+")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "hdr",
        rei(r"\bHDR(?:10)?\b"),
        uniq_concat(value("HDR")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "hdr",
        rei(r"\bSDR\b"),
        uniq_concat(value("SDR")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );

    // ── Codec ─────────────────────────────────────────────────────────────
    p.add(
        "codec",
        rei(r"\b[hx][\. \-]?264\b"),
        value("avc"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "codec",
        rei(r"\b[hx][\. \-]?265\b"),
        value("hevc"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "codec",
        re(r"\b\W264\W\b"),
        value("avc"),
        Opts {
            skip_if_already_found: true,
            remove: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        re(r"\b\W265\W\b"),
        value("hevc"),
        Opts {
            skip_if_already_found: true,
            remove: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        rei(r"\bHEVC10(bit)?\b|\b[xh][\. \-]?265\b"),
        value("hevc"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "codec",
        rei(r"\bhevc(?:\s?10)?\b"),
        value("hevc"),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        rei(r"\bdivx|xvid\b"),
        value("xvid"),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        rei(r"\bavc\b"),
        value("avc"),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        rei(r"\bav1\b"),
        value("av1"),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "codec",
        rei(r"\b(?:mpe?g\d*)\b"),
        value("mpeg"),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    // Cleanup: strip space/dot/dash from codec value
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        if let Some(FieldValue::Str(v)) = ctx.result.get_mut("codec") {
            *v = v.replace([' ', '.', '-'], "");
        }
        None
    }));

    // ── Channels ──────────────────────────────────────────────────────────
    p.add(
        "channels",
        rei(r"5[\.\s]1(?:ch|-S\d+)?\b"),
        uniq_concat(value("5.1")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\b(?:x[2-4]|5[\W]1(?:x[2-4])?)\b"),
        uniq_concat(value("5.1")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\b7[\.\- ]1(.?ch(annel)?)?\b"),
        uniq_concat(value("7.1")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\+?2[\.\s]0(?:x[2-4])?\b"),
        uniq_concat(value("2.0")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\b2\.0\b"),
        uniq_concat(value("2.0")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\b(?:24-bit\s)?stereo\b"),
        uniq_concat(value("stereo")),
        Opts {
            skip_if_already_found: false,
            remove: false,
            ..Default::default()
        },
    );
    p.add(
        "channels",
        rei(r"\bmono\b"),
        uniq_concat(value("mono")),
        Opts {
            skip_if_already_found: false,
            remove: false,
            ..Default::default()
        },
    );

    // ── Audio ─────────────────────────────────────────────────────────────
    let ao = |remove: bool| Opts {
        skip_if_already_found: false,
        remove,
        ..Default::default()
    };
    p.add(
        "audio",
        rei(r"\b(?!.+HR)(DTS.?HD.?Ma(ster)?|DTS.?X)\b"),
        uniq_concat(value("DTS Lossless")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\bDTS(?!(.?HD.?Ma(ster)?|.X)).?(HD.?HR|HD)?\b"),
        uniq_concat(value("DTS Lossy")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\b(Dolby.?)?Atmos\b"),
        uniq_concat(value("Atmos")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\b(True[ .-]?HD|\.True\.)\b"),
        uniq_concat(value("TrueHD")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "audio",
        re(r"\bTRUE\b"),
        uniq_concat(value("TrueHD")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "audio",
        rei(r"\bFLAC(?:\d\.\d)?(?:x\d+)?\b"),
        uniq_concat(value("FLAC")),
        ao(true),
    );
    p.add(
        "audio",
        re(r"DD2?[\+p]|DD Plus|Dolby Digital Plus|DDP(5[ \.\_]1)?|E-?AC-?3(?:-S\d+)?"),
        uniq_concat(value("Dolby Digital Plus")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\bddp(5.1)?"),
        uniq_concat(value("Dolby Digital Plus")),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "audio",
        rei(r"\b(DD|Dolby.?Digital|DolbyD|AC-?3(x2)?(?:-S\d+)?)\b"),
        uniq_concat(value("Dolby Digital")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\bQ?Q?AAC(x?2)?\b"),
        uniq_concat(value("AAC")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\bL?PCM\b"),
        uniq_concat(value("PCM")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\bOPUS(\b|\d)(?!.*[ ._-](\d{3,4}p))"),
        uniq_concat(value("OPUS")),
        ao(true),
    );
    p.add(
        "audio",
        rei(r"\b(H[DQ])?.?(Clean.?Aud(io)?)\b"),
        uniq_concat(value("HQ Clean Audio")),
        ao(true),
    );

    // ── Group (first pass) ─────────────────────────────────────────────────
    p.add("group",
        rei(r"- ?(?!\d+$|S\d+|\d+x|ep?\d+|[^\[]+]$)([^\-. \[]+[^\-. \[)\u{005D}\d][^\-. \[)\u{005D}]*)(?:\[[\w.-]+])?(?=\.\w{2,4}$|$)"),
        arc(tr_none), Opts::defaults().with_remove(false));

    // ── Volumes ───────────────────────────────────────────────────────────
    p.add(
        "volumes",
        rei(r"\bvol(?:s|umes?)?[. -]*(?:\d{1,2}[., +/\\&-]+)+\d{1,2}\b"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );

    p.add_fn(Box::new(|ctx: &mut Ctx| {
        // handle_volumes custom handler
        static RE: OnceCell<Regex> = OnceCell::new();
        let re = RE.get_or_init(|| compile_i(r"\bvol(?:ume)?[. -]*(\d{1,2})"));
        let raw_idx = ctx.matched.get("year").map(|m| m.match_index).unwrap_or(0);
        // match_index is a byte offset; floor it to the nearest char boundary so we
        // never split a multi-byte character (e.g. Cyrillic, CJK).
        let safe_idx = ctx.title.floor_char_boundary(raw_idx.min(ctx.title.len()));
        let search_str = &ctx.title[safe_idx..];
        if let Ok(Some(caps)) = re.captures(search_str)
            && let Some(g1) = caps.get(1)
        {
            let n_str = g1.as_str();
            if let Ok(n) = n_str.parse::<i32>() {
                let g0 = caps.get(0).unwrap();
                let raw = g0.as_str().to_owned();
                let idx = g0.start() + safe_idx;
                ctx.matched.insert(
                    "volumes".into(),
                    MatchInfo {
                        raw_match: raw.clone(),
                        match_index: idx,
                    },
                );
                ctx.result
                    .insert("volumes".into(), FieldValue::Ints(vec![n]));
                return Some(HandlerReturn {
                    raw_match: raw,
                    match_index: idx,
                    remove: true,
                    skip_from_title: false,
                });
            }
        }
        None
    }));

    // ── Pre-Language ──────────────────────────────────────────────────────
    p.add(
        "languages",
        rei(r"\b(temporadas?|completa)\b"),
        uniq_concat(value("es")),
        Opts {
            skip_if_already_found: false,
            ..Default::default()
        },
    );
    p.add(
        "languages",
        rei(r"\b(?:INT[EÉ]GRALE?)\b"),
        uniq_concat(value("fr")),
        Opts {
            skip_if_already_found: false,
            remove: false,
            ..Default::default()
        },
    );
    p.add(
        "languages",
        rei(r"\b(?:Saison)\b"),
        uniq_concat(value("fr")),
        Opts {
            skip_if_already_found: false,
            remove: false,
            ..Default::default()
        },
    );

    // ── Complete (full set) ───────────────────────────────────────────────
    let co_noskip = Opts {
        skip_if_already_found: false,
        ..Default::default()
    };
    p.add(
        "complete",
        rei(r"\b(?:INTEGRALE?|INTÉGRALE?)\b"),
        arc(tr_boolean),
        co_noskip.clone().with_remove(true),
    );
    p.add(
        "complete",
        re(r"(Movie|Complete).Collection"),
        arc(tr_boolean),
        co_noskip.clone().with_remove(true),
    );
    p.add(
        "complete",
        re(r"Complete(.\d{1,2})"),
        arc(tr_boolean),
        co_noskip.clone().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"(?:\bthe\W)?(?:\bcomplete|collection|dvd)?\b[ .]?\bbox[ .-]?set\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"(?:\bthe\W)?(?:\bcomplete|collection|dvd)?\b[ .]?\bmini[ .-]?series\b"),
        arc(tr_boolean),
        Opts::defaults(),
    );
    p.add("complete", rei(r"(?:\bthe\W)?(?:\bcomplete\b|\bfull\b|\ball\b)\b.*\b(?:series|seasons|collection|episodes|set|pack|movies)\b"), arc(tr_boolean), Opts::defaults());
    p.add(
        "complete",
        rei(r"(Top\W+)?\d+\W+(movies?|series|seasons?)\W+Collection"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"(?:\bthe\W)?\bultimate\b[ .]\bcollection\b"),
        arc(tr_boolean),
        Opts {
            skip_if_already_found: false,
            ..Default::default()
        },
    );
    p.add(
        "complete",
        rei(r"\bcollection\b.*\b(?:set|pack|movies)\b"),
        arc(tr_boolean),
        Opts::defaults(),
    );
    p.add(
        "complete",
        rei(r"\bcollection(?:(\s\[|\s\())"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"duology|trilogy|quadr[oi]logy|tetralogy|pentalogy|hexalogy|heptalogy|anthology"),
        arc(tr_boolean),
        Opts {
            skip_if_already_found: false,
            ..Default::default()
        },
    );
    p.add(
        "complete",
        rei(r"\bcompleta\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"\bsaga\b"),
        arc(tr_boolean),
        Opts {
            skip_if_already_found: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "complete",
        rei(r"\b\[Complete\]\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        rei(r"(?<!A )(?<!The )\bComplete\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        re(r"COMPLETE"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "complete",
        re(r"\bkolekcja\b(?:\Wfilm(?:y|ów|ow)?)?"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Seasons ───────────────────────────────────────────────────────────
    p.add(
        "seasons",
        rei(r"(?:complete\W|seasons?\W|\W|^)((?:s\d{1,2}[., +/\\&-]+)+s\d{1,2}\b)"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"(?:complete\W|seasons?\W|\W|^)[(\[]?(s\d{2,}-\d{2,}\b)[)\u{005D}]?"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"(?:complete\W|seasons?\W|\W|^)[(\[]?(s[1-9]-[2-9])[)\u{005D}]?"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"\d+ª(?:.+)?(?:a.?)?\d+ª(?:(?:.+)?(?:temporadas?))"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add("seasons", rei(r"(?:(?:\bthe\W)?\bcomplete\W)?(?:seasons?|[Сс]езони?|temporadas?)[. ]?[-:]?[. ]?[(\[]?((?:\d{1,2}[., /\\&]+)+\d{1,2}\b)[)\u{005D}]?"), arc(tr_range_func), Opts::defaults().with_remove(true));
    p.add("seasons", rei(r"(?:(?:\bthe\W)?\bcomplete\W)?(?:seasons?|[Сс]езони?|temporadas?)[. ]?[-:]?[. ]?[(\[]?((?:\d{1,2}[.-]+)+[1-9]\d?\b)[)\u{005D}]?"), arc(tr_range_func), Opts::defaults().with_remove(true));
    p.add("seasons", rei(r"(?:(?:\bthe\W)?\bcomplete\W)?season[. ]?[(\[]?((?:\d{1,2}[. -]+)+[1-9]\d?\b)[)\u{005D}]?(?!.*\.\w{2,4}$)"), arc(tr_range_func), Opts::defaults().with_remove(true));
    p.add("seasons", rei(r"(?:(?:\bthe\W)?\bcomplete\W)?\bseasons?\b[. -]?(\d{1,2}[. -]?(?:to|thru|and|\+|:)[. -]?\d{1,2})\b"), arc(tr_range_func), Opts::defaults().with_remove(true));
    p.add("seasons", rei(r"(?:(?:\bthe\W)?\bcomplete\W)?(?:saison|seizoen|season|series|temp(?:orada)?):?[. ]?(\d{1,2})\b"), array(arc(tr_integer)), Opts::defaults());
    p.add(
        "seasons",
        rei(r"(\d{1,2})(?:-?й)?[. _]?(?:[Сс]езон|sez(?:on)?)(?:\W?\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"[Сс]езон:?[. _]?№?(\d{1,2})(?!\d)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"(?:\D|^)(\d{1,2})Â?[°ºªa]?[. ]*temporada"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"t(\d{1,3})(?:[ex]+|$)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"(?:(?:\bthe\W)?\bcomplete)?(?<![a-z])\bs(\d{1,3})(?:[\Wex]|\d{2}\b|$)"),
        array(arc(tr_integer)),
        Opts {
            skip_if_already_found: false,
            remove: false,
            ..Default::default()
        },
    );
    p.add(
        "seasons",
        rei(r"(?:(?:\bthe\W)?\bcomplete\W)?(?:\W|^)(\d{1,2})[. ]?(?:st|nd|rd|th)[. ]*season"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"(?<=S)\d{2}(?=E\d+)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        re(r"(?:\D|^)(\d{1,2})[xх]\d{1,3}(?:\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"\bSn([1-9])(?:\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"[\[(](\d{1,2})\.\d{1,3}[)\u{005D}]"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"-\s?(\d{1,2})\.\d{2,3}\s?-"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"(?:^|\/)(\d{1,2})-\d{2}\b(?!-\d)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"[^\w-](\d{1,2})-\d{2}(?=\.\w{2,4}$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        re(r"\b(\d{2})[ ._]\d{2}(?:.F)?\.\w{2,4}$"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        rei(r"\bEp(?:isode)?\W+(\d{1,2})\.\d{1,3}\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        rei(r"\bSeasons?\b.*\b(\d{1,2}-\d{1,2})\b"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "seasons",
        rei(r"(?:\W|^)(\d{1,2})(?:e|ep)\d{1,3}(?:\W|$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "seasons",
        rei(r"\bs(\d{1,4})"),
        array(arc(tr_integer)),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "seasons",
        rei(r"\bТВ-(\d{1,2})\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );

    // ── Episodes ──────────────────────────────────────────────────────────
    p.add(
        "episodes",
        rei(r"(?:[\W\d]|^)e[ .]?[(\[]?(\d{1,3}(?:[ .-]*(?:[&+]|e){1,2}[ .]?\d{1,3})+)(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?:[\W\d]|^)ep[ .]?[(\[]?(\d{1,3}(?:[ .-]*(?:[&+]|ep){1,2}[ .]?\d{1,3})+)(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?:[\W\d]|^)\d+[xх][ .]?[(\[]?(\d{1,3}(?:[ .]?[xх][ .]?\d{1,3})+)(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"Серии:\s+(\d+)\s+(?:of|из|iz)\s+\d+\b"),
        arc(tr_range_x_of_y),
        Opts::defaults(),
    );
    p.add("episodes", rei(r"(?:[\W\d]|^)(?:episodes?|[Сс]ерии:?)[ .]?[(\[]?(\d{1,3}(?:[ .+]*[&+][ .]?\d{1,3})+)(?:\W|$)"), arc(tr_range_func), Opts::defaults());
    p.add(
        "episodes",
        rei(r"[(\[]?(?:\D|^)(\d{1,3}[ .]?ao[ .]?\d{1,3})[)\u{005D}]?(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add("episodes", rei(r"(?:[\W\d]|^)(?:e|eps?|episodes?|[Сс]ерии:?|\d+[xх])[ .]*[(\[]?(\d{1,3}(?:-\d{1,3})+)(?:\W|$)"), arc(tr_range_func), Opts::defaults());
    p.add(
        "episodes",
        rei(r"(?:\W|^)(\d{1,3}(?:[ .]*~[ .]*\d{1,3})+)(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"\bE\d{1,4}\s*à\s*E\d{1,4}\b"),
        arc(tr_range_func),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "episodes",
        rei(r"[st]\d{1,2}[. ]?[xх-]?[. ]?(?:e|x|х|ep|-|\.)[. ]?(\d{1,4})(?:[abc]|v0?[1-4]|\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "episodes",
        rei(r"\b[st]\d{2}(\d{2})\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"-\s(\d{1,3}[ .]*-[ .]*\d{1,3})(?!-\d)(?:\W|$)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"s\d{1,2}\s?\((\d{1,3}[ .]*-[ .]*\d{1,3})\)"),
        arc(tr_range_func),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"(?:^|\/)\d{1,2}-(\d{2})\b(?!-\d)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"(?<!\d-)\b\d{1,2}-(\d{2})(?=\.\w{2,4}$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?<=^\[[^\u{005D}]+\].+?[. ])-[. ]+(\d{1,4})[. ]+(?=\W)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add("episodes", rei(r"(?<!(?:seasons?|[Сс]езони?)\W{0,10})(?:[ .(\[-]|^)(\d{1,3}(?:[ .]?[,&+~][ .]?\d{1,3})+)(?:[ .)\u{005D}-]|$)"), arc(tr_range_func), Opts::defaults());
    p.add("episodes", rei(r"(?<!(?:seasons?|[Сс]езони?)\W{0,10})(?:[ .(\[-]|^)(\d{1,3}(?:-\d{1,3})+)(?:[ .)(\u{005D}]|-\D|$)"), arc(tr_range_func), Opts::defaults());
    p.add(
        "episodes",
        rei(r"\bEp(?:isode)?\W+\d{1,2}\.(\d{1,3})\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"Ep.\d+.-.\d+"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add("episodes", rei(r"(?:\b[ée]p?(?:isode)?|[Ээ]пизод|[Сс]ер(?:ии|ия|\.)?|cap(?:itulo)?|epis[oó]dio)[. ]?[-:#№]?[. ]?(\d{1,4})(?:[abc]|v0?[1-4]|\W|$)"), array(arc(tr_integer)), Opts::defaults());
    p.add(
        "episodes",
        rei(r"\b(\d{1,3})(?:-?я)?[ ._-]*(?:ser(?:i?[iyj]a|\b)|[Сс]ер(?:ии|ия|\.)?)\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"(?:\D|^)\d{1,2}[. ]?[xх][. ]?(\d{1,3})(?:[abc]|v0?[1-4]|\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"(?<=S\d{2}E)\d+"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"[\[(]\d{1,2}\.(\d{1,3})[)\u{005D}]"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"\b[Ss]\d{1,2}[ .](\d{1,2})\b"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"-\s?\d{1,2}\.(\d{2,3})\s?-"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?:\[|\()(\d+)\s(?:of|из|iz)\s\d+(?:\]|\))"),
        arc(tr_range_x_of_y),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?<!\d)(\d{1,3})[. ]?(?:of|из|iz)[. ]?\d{1,3}(?=\D|$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"\b\d{2}[ ._-](\d{2})(?:.F)?\.\w{2,4}$"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        re(r"(?<!^)\[(?!720|1080)([\.\-\s\W]\d{2,3}[\.\-\s\W])](?!(?:\.\w{2,4})?$)"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(\d+)(?=.?\[(\[A-Z0-9]{8})])"),
        array(arc(tr_integer)),
        Opts::defaults(),
    );
    p.add(
        "episodes",
        rei(r"(?<![xh])\b264\b|\b265\b"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "episodes",
        re(r"(?<!\bMovie\s-\s)(?<=\s-\s)\d+(?=\s[-(\s])"),
        array(arc(tr_integer)),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );
    p.add(
        "episodes",
        rei(r"(?:\W|^)(?:\d+)?(?:e|ep)(\d{1,3})(?:\W|$)"),
        array(arc(tr_integer)),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "episodes",
        rei(r"\d+.-.\d+TV"),
        arc(tr_range_func),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "episodes",
        rei(r"E(\d+)\b"),
        array(arc(tr_integer)),
        Opts {
            skip_if_already_found: true,
            remove: false,
            ..Default::default()
        },
    );
    p.add(
        "episodes",
        rei(r"\b\d{1,4}-\d{1,4}\b"),
        arc(tr_range_func),
        Opts {
            skip_if_already_found: true,
            remove: false,
            ..Default::default()
        },
    );

    // handle_episodes: last-resort pattern scan
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        if ctx.result.contains_key("episodes")
            && let Some(FieldValue::Ints(v)) = ctx.result.get("episodes")
                && !v.is_empty() {
                    return None;
                }
        let start_indexes: Vec<usize> = ["year", "seasons"]
            .iter()
            .filter_map(|k| ctx.matched.get(*k).map(|m| m.match_index))
            .collect();
        let end_indexes: Vec<usize> = ["resolution", "quality", "codec", "audio"]
            .iter()
            .filter_map(|k| ctx.matched.get(*k).map(|m| m.match_index))
            .collect();

        let start_index = start_indexes.iter().copied().min().unwrap_or(0);
        let end_index = end_indexes.iter().copied().min().unwrap_or(ctx.title.len());

        let safe_end = ctx.title.floor_char_boundary(end_index.min(ctx.title.len()));
        let safe_start = ctx.title.floor_char_boundary(start_index.min(safe_end));
        let beginning = &ctx.title[..safe_end];
        let middle = &ctx.title[safe_start..safe_end];

        static BEG_RE: OnceCell<Regex> = OnceCell::new();
        static MID_RE: OnceCell<Regex> = OnceCell::new();
        static DIGIT_RE: OnceCell<Regex> = OnceCell::new();
        let beg_re = BEG_RE.get_or_init(|| compile_i(
            r"(?<!movie\W{0,5}|film\W{0,5})(?:[ .]+-[ .]+|[(\[][ .]*)(\d{1,4})(?:a|b|v\d|\.\d)?(?:\W|$)(?!movie|film|\d+)(?<!\[(?:480|720|1080)\])"
        ));
        let mid_re = MID_RE.get_or_init(|| compile_i(
            r"^(?:[(\[-][ .]?)?(\d{1,4})(?:a|b|v\d)?(?:\W|$)(?!movie|film)(?!\[(480|720|1080)\])"
        ));
        let digit_re = DIGIT_RE.get_or_init(|| compile(r"\d+"));

        let beg_caps = beg_re.captures(beginning).ok().flatten();
        let mid_caps = if beg_caps.is_none() {
            mid_re.captures(middle).ok().flatten()
        } else {
            None
        };
        let caps_opt = beg_caps.or(mid_caps);

        if let Some(caps) = caps_opt {
            let g1 = caps.get(1).map(|m| m.as_str().to_owned()).unwrap_or_default();
            let full = caps.get(0).map(|m| m.as_str().to_owned()).unwrap_or_default();
            let nums: Vec<i32> = digit_re
                .find_iter(&g1)
                .filter_map(|r| r.ok())
                .filter_map(|m| m.as_str().parse().ok())
                .collect();
            if !nums.is_empty() {
                let idx = ctx.title.find(full.as_str()).unwrap_or(0);
                ctx.result.insert("episodes".into(), FieldValue::Ints(nums));
                return Some(HandlerReturn { raw_match: full, match_index: idx, remove: false, skip_from_title: false });
            }
        }
        None
    }));

    // handle_anime_eps
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        let already = ctx
            .result
            .get("episodes")
            .and_then(|v| {
                if let FieldValue::Ints(x) = v {
                    Some(x)
                } else {
                    None
                }
            })
            .is_some_and(|v| !v.is_empty());
        if already {
            return None;
        }
        static ANIME_RE: OnceCell<Regex> = OnceCell::new();
        static EP_RE: OnceCell<Regex> = OnceCell::new();
        let anime_re = ANIME_RE.get_or_init(|| compile(r"One.*?Piece|Bleach|Naruto"));
        let ep_re = EP_RE.get_or_init(|| compile(r"\b\d{1,4}\b"));
        if anime_re.is_match(&ctx.title).unwrap_or(false)
            && let Ok(Some(m)) = ep_re.find(&ctx.title)
        {
            let s = m.as_str().to_owned();
            if let Ok(n) = s.parse::<i32>() {
                let idx = m.start();
                ctx.result
                    .insert("episodes".into(), FieldValue::Ints(vec![n]));
                return Some(HandlerReturn {
                    raw_match: s,
                    match_index: idx,
                    remove: false,
                    skip_from_title: false,
                });
            }
        }
        None
    }));

    // ── Country ───────────────────────────────────────────────────────────
    p.add(
        "country",
        re(r"\b(US|UK|AU|NZ|CA)\b"),
        value("$1"),
        Opts::defaults(),
    );

    // ── Languages ─────────────────────────────────────────────────────────
    let lo = |remove: bool, skip: bool| Opts {
        skip_if_already_found: false,
        remove,
        skip_from_title: skip,
        skip_if_first: false,
    };
    let lof = |remove: bool, skip_from: bool, skip_if_first: bool| Opts {
        skip_if_already_found: false,
        remove,
        skip_from_title: skip_from,
        skip_if_first,
    };

    // English
    p.add(
        "languages",
        rei(r"\bengl?(?:sub[A-Z]*)?\b"),
        uniq_concat(value("en")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\beng?sub[A-Z]*\b"),
        uniq_concat(value("en")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bing(?:l[eéê]s)?\b"),
        uniq_concat(value("en")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\besub\b"),
        uniq_concat(value("en")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\benglish\W+(?:subs?|sdh|hi)\b"),
        uniq_concat(value("en")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\beng?\b"),
        uniq_concat(value("en")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\benglish?\b"),
        uniq_concat(value("en")),
        lof(false, false, true),
    );
    // Japanese
    p.add(
        "languages",
        rei(r"\b(?:JP|JAP|JPN)\b"),
        uniq_concat(value("ja")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(japanese|japon[eê]s)\b"),
        uniq_concat(value("ja")),
        lof(false, false, true),
    );
    // Korean
    p.add(
        "languages",
        rei(r"\b(?:KOR|kor[ .-]?sub)\b"),
        uniq_concat(value("ko")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(korean|coreano)\b"),
        uniq_concat(value("ko")),
        lof(false, false, true),
    );
    // Chinese
    p.add(
        "languages",
        rei(r"\b(?:traditional\W*chinese|chinese\W*traditional)(?:\Wchi)?\b"),
        uniq_concat(value("zh")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\bzh-hant\b"),
        uniq_concat(value("zh")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:mand[ae]rin|ch[sn])\b"),
        uniq_concat(value("zh")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"(?<!shang-?)\bCH(?:I|T)\b"),
        uniq_concat(value("zh")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(chinese|chin[eê]s)\b"),
        uniq_concat(value("zh")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\bzh-hans\b"),
        uniq_concat(value("zh")),
        lo(false, false),
    );
    // French
    p.add(
        "languages",
        rei(r"\bFR(?:a|e|anc[eê]s|VF[FQIB2]?)\b"),
        uniq_concat(value("fr")),
        lo(false, true),
    );
    p.add(
        "languages",
        re(r"\b\[?(VF[FQRIB2]?\]?\b|(VOST)?FR2?)\b"),
        uniq_concat(value("fr")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\b(TRUE|SUB).?FRENCH\b|\bFRENCH\b|\bFre?\b"),
        uniq_concat(value("fr")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\b(VOST(?:FR?|A)?)\b"),
        uniq_concat(value("fr")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(VF[FQIB2]?|(TRUE|SUB).?FRENCH|(VOST)?FR2?)\b"),
        uniq_concat(value("fr")),
        Opts {
            skip_if_already_found: true,
            remove: true,
            ..Default::default()
        },
    );
    // Spanish / Latino
    p.add(
        "languages",
        rei(r"\bspanish\W?latin|american\W*(?:spa|esp?)"),
        uniq_concat(value("la")),
        lo(true, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:\bla\b.+(?:cia\b))"),
        uniq_concat(value("es")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:audio.)?lat(?:in?|ino)?\b"),
        uniq_concat(value("la")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:audio.)?(?:ESP?|spa|(en[ .]+)?espa[nñ]ola?|castellano)\b"),
        uniq_concat(value("es")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bes(?=[ .,/-]+(?:[A-Z]{2}[ .,/-]+){2,})\b"),
        uniq_concat(value("es")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?<=[ .,/-]{1,5}(?:[A-Z]{2}[ .,/-]{1,5}){2,5})es\b"),
        uniq_concat(value("es")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?<=[ .,/-]{1,5}[A-Z]{2}[ .,/-]{1,5})es(?=[ .,/-]+[A-Z]{2}[ .,/-]+)\b"),
        uniq_concat(value("es")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bes(?=\.(?:ass|ssa|srt|sub|idx)$)"),
        uniq_concat(value("es")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bspanish\W+subs?\b"),
        uniq_concat(value("es")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(spanish|espanhol)\b"),
        uniq_concat(value("es")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\b[\.\s\[]?Sp[\.\s\u{005D}]?\b"),
        uniq_concat(value("es")),
        lo(true, false),
    );
    // Portuguese
    p.add(
        "languages",
        rei(r"\b(?:p[rt]|en|port)[. (\\/-]*BR\b"),
        uniq_concat(value("pt")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\bbr(?:a|azil|azilian)\W+(?:pt|por)\b"),
        uniq_concat(value("pt")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:leg(?:endado|endas?)?|dub(?:lado)?|portugu[eèê]se?)[. -]*BR\b"),
        uniq_concat(value("pt")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bleg(?:endado|endas?)\b"),
        uniq_concat(value("pt")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bportugu[eèê]s[ea]?\b"),
        uniq_concat(value("pt")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bPT[. -]*(?:PT|ENG?|sub(?:s|titles?))\b"),
        uniq_concat(value("pt")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bpt(?=\.(?:ass|ssa|srt|sub|idx)$)"),
        uniq_concat(value("pt")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bPT\b"),
        uniq_concat(value("pt")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\bpor\b"),
        uniq_concat(value("pt")),
        lo(false, true),
    );
    // Italian
    p.add(
        "languages",
        rei(r"\b-?ITA\b"),
        uniq_concat(value("it")),
        lo(true, false),
    );
    p.add(
        "languages",
        re(r"\b(?<!w{3}\.\w{1,30}\.)IT(?=[ .,/-]+(?:[a-zA-Z]{2}[ .,/-]+){2,})\b"),
        uniq_concat(value("it")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bit(?=\.(?:ass|ssa|srt|sub|idx)$)"),
        uniq_concat(value("it")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bitaliano?\b"),
        uniq_concat(value("it")),
        lof(false, false, true),
    );
    // Greek
    p.add(
        "languages",
        rei(r"\bgreek[ .-]*(?:audio|lang(?:uage)?|subs?(?:titles?)?)?\b"),
        uniq_concat(value("el")),
        lof(false, false, true),
    );
    // German
    p.add(
        "languages",
        rei(r"\b(?:GER|DEU)\b"),
        uniq_concat(value("de")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bde(?=[ .,/-]+(?:[A-Z]{2}[ .,/-]+){2,})\b"),
        uniq_concat(value("de")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?<=[ .,/-]{1,5}(?:[A-Z]{2}[ .,/-]{1,5}){2,5})de\b"),
        uniq_concat(value("de")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?<=[ .,/-]{1,5}[A-Z]{2}[ .,/-]{1,5})de(?=[ .,/-]+[A-Z]{2}[ .,/-]+)\b"),
        uniq_concat(value("de")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bde(?=\.(?:ass|ssa|srt|sub|idx)$)"),
        uniq_concat(value("de")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(german|alem[aã]o)\b"),
        uniq_concat(value("de")),
        lof(false, false, true),
    );
    // Russian / Ukrainian
    p.add(
        "languages",
        rei(r"\bRUS?\b"),
        uniq_concat(value("ru")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(russian|russo)\b"),
        uniq_concat(value("ru")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"UKR\b"),
        uniq_concat(value("uk")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bukrainian\b"),
        uniq_concat(value("uk")),
        lof(false, false, true),
    );
    // South Asian
    p.add(
        "languages",
        rei(r"\bhin(?:di)?\b"),
        uniq_concat(value("hi")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)tel(?!\W*aviv)|telugu)\b"),
        uniq_concat(value("te")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\bt[aâ]m(?:il)?\b"),
        uniq_concat(value("ta")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)MAL(?:ay)?|malayalam)\b"),
        uniq_concat(value("ml")),
        lof(true, false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)KAN(?:nada)?|kannada)\b"),
        uniq_concat(value("kn")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)MAR(?:a(?:thi)?)?|marathi)\b"),
        uniq_concat(value("mr")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)GUJ(?:arati)?|gujarati)\b"),
        uniq_concat(value("gu")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)PUN(?:jabi)?|punjabi)\b"),
        uniq_concat(value("pa")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)BEN(?!.\bThe|and|of\b)(?:gali)?|bengali)\b"),
        uniq_concat(value("bn")),
        lof(false, false, true),
    );
    // Eastern European
    p.add(
        "languages",
        re(r"\b(?<!YTS\.)LT\b"),
        uniq_concat(value("lt")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\blithuanian\b"),
        uniq_concat(value("lt")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\blatvian\b"),
        uniq_concat(value("lv")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\bestonian\b"),
        uniq_concat(value("et")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)PL|pol)\b"),
        uniq_concat(value("pl")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(polish|polon[eê]s|polaco)\b"),
        uniq_concat(value("pl")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\b(PLDUB|PLSUB|DUBPL|DubbingPL|LekPL|LektorPL)\b"),
        uniq_concat(value("pl")),
        lo(true, false),
    );
    p.add(
        "languages",
        rei(r"\bCZ[EH]?\b"),
        uniq_concat(value("cs")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\bczech\b"),
        uniq_concat(value("cs")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\bslo(?:vak|vakian|subs|[\u{005D}_)]?\.\w{2,4}$)\b"),
        uniq_concat(value("sk")),
        lo(false, true),
    );
    p.add(
        "languages",
        re(r"\bHU\b"),
        uniq_concat(value("hu")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bHUN(?:garian)?\b"),
        uniq_concat(value("hu")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bROM(?:anian)?\b"),
        uniq_concat(value("ro")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bRO(?=[ .,/-]*(?:[A-Z]{2}[ .,/-]+)*sub)"),
        uniq_concat(value("ro")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bbul(?:garian)?\b"),
        uniq_concat(value("bg")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:srp|serbian)\b"),
        uniq_concat(value("sr")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:HRV|croatian)\b"),
        uniq_concat(value("hr")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bHR(?=[ .,/-]*(?:[A-Z]{2}[ .,/-]+)*sub)\b"),
        uniq_concat(value("hr")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bslovenian\b"),
        uniq_concat(value("sl")),
        lo(false, true),
    );
    // Dutch / Scandinavian
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)NL|dut|holand[eê]s)\b"),
        uniq_concat(value("nl")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bdutch\b"),
        uniq_concat(value("nl")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bflemish\b"),
        uniq_concat(value("nl")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(?:DK|danska|dansub|nordic)\b"),
        uniq_concat(value("da")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(danish|dinamarqu[eê]s)\b"),
        uniq_concat(value("da")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bdan\b(?=.*\.(?:srt|vtt|ssa|ass|sub|idx)$)"),
        uniq_concat(value("da")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!Sci-)(?<!w{3}\.\w{1,30}\.)FI|finsk|finsub|nordic)\b"),
        uniq_concat(value("fi")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\bfinnish\b"),
        uniq_concat(value("fi")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:(?<!w{3}\.\w{1,30}\.)SE|swe|swesubs?|sv(?:ensk)?|nordic)\b"),
        uniq_concat(value("sv")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(swedish|sueco)\b"),
        uniq_concat(value("sv")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:NOR|norsk|norsub|nordic)\b"),
        uniq_concat(value("no")),
        lo(false, false),
    );
    p.add(
        "languages",
        rei(r"\b(norwegian|noruegu[eê]s|bokm[aå]l|nob|nor(?=[\u{005D}_)]?\.\w{2,4}$))\b"),
        uniq_concat(value("no")),
        lo(false, true),
    );
    // Arabic / Turkish / Southeast Asian / Middle Eastern
    p.add(
        "languages",
        rei(r"\b(?:arabic|[aá]rabe|ara)\b"),
        uniq_concat(value("ar")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\barab.*(?:audio|lang(?:uage)?|sub(?:s|titles?)?)\b"),
        uniq_concat(value("ar")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bar(?=\.(?:ass|ssa|srt|sub|idx)$)"),
        uniq_concat(value("ar")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:turkish|tur(?:co)?)\b"),
        uniq_concat(value("tr")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(TİVİBU|tivibu|bitturk(.net)?|turktorrent)\b"),
        uniq_concat(value("tr")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bvietnamese\b|\bvie(?=[\u{005D}_)]?\.\w{2,4}$)"),
        uniq_concat(value("vi")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\bind(?:onesian)?\b"),
        uniq_concat(value("id")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(thai|tailand[eê]s)\b"),
        uniq_concat(value("th")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        re(r"\b(THA|tha)\b"),
        uniq_concat(value("th")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(?:malay|may(?=[\u{005D}_)]?\.\w{2,4}$)|(?<=subs?\([a-z,]{1,50})may)\b"),
        uniq_concat(value("ms")),
        lof(false, false, true),
    );
    p.add(
        "languages",
        rei(r"\bheb(?:rew|raico)?\b"),
        uniq_concat(value("he")),
        lo(false, true),
    );
    p.add(
        "languages",
        rei(r"\b(persian|persa)\b"),
        uniq_concat(value("fa")),
        lo(false, true),
    );

    // Unicode script detection
    p.add(
        "languages",
        re(r"[\x{3040}-\x{30ff}]+"),
        uniq_concat(value("ja")),
        lo(false, true),
    ); // Japanese kana
    p.add(
        "languages",
        re(r"[\x{3400}-\x{4dbf}]+"),
        uniq_concat(value("zh")),
        lo(false, true),
    ); // CJK ext A
    p.add(
        "languages",
        re(r"[\x{4e00}-\x{9fff}]+"),
        uniq_concat(value("zh")),
        lo(false, true),
    ); // CJK unified
    p.add(
        "languages",
        re(r"[\x{f900}-\x{faff}]+"),
        uniq_concat(value("zh")),
        lo(false, true),
    ); // CJK compat
    p.add(
        "languages",
        re(r"[\x{ff66}-\x{ff9f}]+"),
        uniq_concat(value("ja")),
        lo(false, true),
    ); // Halfwidth Katakana
    p.add(
        "languages",
        re(r"[\x{0600}-\x{06ff}]+"),
        uniq_concat(value("ar")),
        lo(false, true),
    ); // Arabic
    p.add(
        "languages",
        re(r"[\x{0750}-\x{077f}]+"),
        uniq_concat(value("ar")),
        lo(false, true),
    ); // Arabic supplement
    p.add(
        "languages",
        re(r"[\x{0c80}-\x{0cff}]+"),
        uniq_concat(value("kn")),
        lo(false, true),
    ); // Kannada
    p.add(
        "languages",
        re(r"[\x{0d00}-\x{0d7f}]+"),
        uniq_concat(value("ml")),
        lo(false, true),
    ); // Malayalam
    p.add(
        "languages",
        re(r"[\x{0e00}-\x{0e7f}]+"),
        uniq_concat(value("th")),
        lo(false, true),
    ); // Thai
    p.add(
        "languages",
        re(r"[\x{0900}-\x{097f}]+"),
        uniq_concat(value("hi")),
        lo(false, true),
    ); // Devanagari (Hindi)
    p.add(
        "languages",
        re(r"[\x{0980}-\x{09ff}]+"),
        uniq_concat(value("bn")),
        lo(false, true),
    ); // Bengali
    p.add(
        "languages",
        re(r"[\x{0a00}-\x{0a7f}]+"),
        uniq_concat(value("gu")),
        lo(false, true),
    ); // Gujarati

    // infer_language_based_on_naming
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        let has_pt_es = ctx
            .result
            .get("languages")
            .and_then(|v| {
                if let FieldValue::Strs(l) = v {
                    Some(l)
                } else {
                    None
                }
            })
            .is_some_and(|l| l.contains(&"pt".to_string()) || l.contains(&"es".to_string()));
        if !has_pt_es {
            let ep_raw = ctx
                .matched
                .get("episodes")
                .map(|m| m.raw_match.as_str())
                .unwrap_or("");
            static CAP_RE: OnceCell<Regex> = OnceCell::new();
            static DUB_RE: OnceCell<Regex> = OnceCell::new();
            let capitulo = CAP_RE.get_or_init(|| compile_i(r"capitulo|ao"));
            let dublado = DUB_RE.get_or_init(|| compile_i(r"dublado"));
            if capitulo.is_match(ep_raw).unwrap_or(false)
                || dublado.is_match(&ctx.title).unwrap_or(false)
            {
                let mut langs: Vec<String> = match ctx.result.get("languages") {
                    Some(FieldValue::Strs(v)) => v.clone(),
                    _ => vec![],
                };
                langs.push("pt".to_string());
                ctx.result
                    .insert("languages".into(), FieldValue::Strs(langs));
            }
        }
        None
    }));

    // ── Subbed ────────────────────────────────────────────────────────────
    p.add(
        "subbed",
        rei(r"\bmulti(?:ple)?[ .-]*(?:su?$|sub\w*|dub\w*)\b|msub"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "subbed",
        rei(r"\b(?:Official.*?|Dual-?)?sub(s|bed)?\b"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );

    // ── Dubbed ────────────────────────────────────────────────────────────
    let dubbed_no_skip = |remove: bool| Opts {
        skip_if_already_found: false,
        remove,
        ..Default::default()
    };
    p.add(
        "dubbed",
        rei(r"[\[(\s]?\bmulti(?:ple)?[ .-]*(?:lang(?:uages?)?|audio|VF2)\b\][\[(\s]?"),
        arc(tr_boolean),
        dubbed_no_skip(true),
    );
    p.add(
        "dubbed",
        rei(r"\btri(?:ple)?[ .-]*(?:audio|dub\w*)\b"),
        arc(tr_boolean),
        dubbed_no_skip(false),
    );
    p.add(
        "dubbed",
        rei(r"\bdual[ .-]*(?:au?$|[aá]udio|line)\b"),
        arc(tr_boolean),
        dubbed_no_skip(false),
    );
    p.add(
        "dubbed",
        rei(r"\bdual\b(?![ .-]*sub)"),
        arc(tr_boolean),
        dubbed_no_skip(false),
    );
    p.add(
        "dubbed",
        rei(r"\b(fan\s?dub)\b"),
        arc(tr_boolean),
        Opts {
            skip_if_already_found: false,
            remove: true,
            skip_from_title: true,
            ..Default::default()
        },
    );
    p.add(
        "dubbed",
        rei(r"\b(Fan.*)?(?:DUBBED|dublado|dubbing|DUBS?)\b"),
        arc(tr_boolean),
        dubbed_no_skip(true),
    );
    p.add(
        "dubbed",
        rei(r"\b(?!.*\bsub(s|bed)?\b)([ _\-\[(\.])?(dual|multi)([ _\-\[(\.])?(audio)\b"),
        arc(tr_boolean),
        dubbed_no_skip(true),
    );
    p.add(
        "dubbed",
        rei(r"\b(JAP?(anese)?|ZH)\+ENG?(lish)?|ENG?(lish)?\+(JAP?(anese)?|ZH)\b"),
        arc(tr_boolean),
        dubbed_no_skip(true),
    );
    p.add(
        "dubbed",
        rei(r"\bMULTi\b"),
        arc(tr_boolean),
        dubbed_no_skip(true),
    );

    // handle_group: remove group if it overlaps other matches
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        if let Some(group_info) = ctx.matched.get("group").cloned()
            && group_info.raw_match.starts_with('[')
            && group_info.raw_match.ends_with(']')
        {
            let end_idx = group_info.match_index + group_info.raw_match.len();
            let overlaps = ctx
                .matched
                .iter()
                .any(|(k, v)| k != "group" && v.match_index < end_idx);
            if overlaps {
                ctx.result.remove("group");
            }
        }
        None
    }));

    // ── 3D ────────────────────────────────────────────────────────────────
    let opts_3d = Opts {
        skip_if_already_found: false,
        remove: false,
        skip_if_first: true,
        ..Default::default()
    };
    p.add(
        "3d",
        rei(r"\b[12]\d{3}\b.*\b(3d|sbs|half[ .-]ou|half[ .-]sbs)\b"),
        arc(tr_boolean),
        opts_3d.clone(),
    );
    p.add(
        "3d",
        rei(r"\b((Half.)?SBS|HSBS)\b"),
        arc(tr_boolean),
        opts_3d.clone(),
    );
    p.add("3d", rei(r"\bBluRay3D\b"), arc(tr_boolean), opts_3d.clone());
    p.add("3d", rei(r"\bBD3D\b"), arc(tr_boolean), opts_3d.clone());
    p.add("3d", re(r"\b3D\b"), arc(tr_boolean), opts_3d);

    // ── Size ──────────────────────────────────────────────────────────────
    p.add(
        "size",
        rei(r"\b(\d+(\.\d+)?\s?(MB|GB|TB))\b"),
        arc(tr_none),
        Opts::defaults().with_remove(true),
    );

    // ── Site (late) ───────────────────────────────────────────────────────
    p.add(
        "site",
        rei(r"\b(?:www?.?)?(?:\w+\-)?\w+[\.\s](?:com|org|net|ms|tv|mx|co|\.party|vip|nu|pics)\b"),
        value("$1"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "site",
        rei(r"rarbg|torrentleech|(?:the)?piratebay"),
        value("$1"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "site",
        rei(r"\[(\[^\]]+\.[^\u{005D}]+)\](?=\.\w{2,4}$|\s)"),
        value("$1"),
        Opts::defaults().with_remove(true),
    );

    // ── Networks ──────────────────────────────────────────────────────────
    p.add(
        "network",
        rei(r"\bATVP?\b"),
        value("Apple TV"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bAMZN\b"),
        value("Amazon"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bNF|Netflix\b"),
        value("Netflix"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bNICK(elodeon)?\b"),
        value("Nickelodeon"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bDSNY?P?\b"),
        value("Disney"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bH(MAX|BO)\b"),
        value("HBO"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bHULU\b"),
        value("Hulu"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bCBS\b"),
        value("CBS"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bNBC\b"),
        value("NBC"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bAMC\b"),
        value("AMC"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bPBS\b"),
        value("PBS"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\b(Crunchyroll|[. -]CR[. -])\b"),
        value("Crunchyroll"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        re(r"\bVICE\b"),
        value("VICE"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bSony\b"),
        value("Sony"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bHallmark\b"),
        value("Hallmark"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bAdult.?Swim\b"),
        value("Adult Swim"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bAnimal.?Planet|ANPL\b"),
        value("Animal Planet"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "network",
        rei(r"\bCartoon.?Network(.TOONAMI.BROADCAST)?\b"),
        value("Cartoon Network"),
        Opts::defaults().with_remove(true),
    );

    // ── Extension ─────────────────────────────────────────────────────────
    p.add("extension",
        rei(r"\.(3g2|3gp|avi|flv|mkv|mk3d|mov|mp2|mp4|m4v|mpe|mpeg|mpg|mpv|webm|wmv|ogm|divx|ts|m2ts|iso|vob|sub|idx|ttxt|txt|smi|srt|ssa|ass|vtt|nfo|html)$"),
        arc(tr_lowercase), Opts::defaults().with_remove(true));
    p.add(
        "audio",
        rei(r"\bMP3\b"),
        uniq_concat(value("MP3")),
        Opts {
            skip_if_already_found: false,
            remove: true,
            ..Default::default()
        },
    );

    // ── Group (final passes) ──────────────────────────────────────────────
    p.add(
        "group",
        re(r"\(([\w-]+)\)(?:$|\.\w{2,4}$)"),
        arc(tr_none),
        Opts::defaults(),
    );
    p.add(
        "group",
        re(r"\b(INFLATE|DEFLATE)\b"),
        value("$1"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "group",
        rei(r"\b(?:Erai-raws|Erai-raws\.com)\b"),
        value("Erai-raws"),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "group",
        re(r"^\[(\[^\[\]]+)]"),
        arc(tr_none),
        Opts::defaults(),
    );

    // handle_group_exclusion
    p.add_fn(Box::new(|ctx: &mut Ctx| {
        if let Some(FieldValue::Str(g)) = ctx.result.get("group")
            && (g == "-" || g.is_empty())
        {
            ctx.result.remove("group");
        }
        None
    }));

    // ── Misc ──────────────────────────────────────────────────────────────
    p.add(
        "trash",
        rei(r"acesse o original"),
        arc(tr_boolean),
        Opts::defaults().with_remove(true),
    );
    p.add(
        "title",
        rei(r"\bHigh.?Quality\b"),
        arc(tr_none),
        Opts::defaults()
            .with_remove(true)
            .with_skip_from_title(true),
    );
}

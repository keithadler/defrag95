// defrag95 - cluster map data loaded from results/clustermap.json
// Keith Adler
//
// A deliberately small reader for the one JSON shape the simulator emits.
// No dependency beyond the C++ standard library, so the UI builds with
// nothing but Turbo Vision on the include path.

#ifndef DEFRAG95_MAPDATA_H
#define DEFRAG95_MAPDATA_H

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace defrag95 {

enum Category {
    catFree = 0, catBoot, catApp, catSwap, catWarm, catChurn, catCold, catFragmented,
    catCount
};

struct Layout {
    std::string name;
    std::string label;
    std::vector<unsigned char> cells;
    double bootMs = 0, wordMs = 0, pagingMs = 0, dayMs = 0;
    double extentsPerFile = 0, pctFragmented = 0;
    long freeHoles = 0;
};

struct MapData {
    std::string drive;
    long clusters = 0;
    long clusterKb = 32;
    std::vector<Layout> layouts;
    std::string error;

    bool ok() const { return error.empty() && !layouts.empty(); }
    const Layout *find(const std::string &n) const {
        for (const auto &l : layouts)
            if (l.name == n) return &l;
        return nullptr;
    }
};

// --- the world's most specialised JSON reader --------------------------------

namespace detail {

inline std::string slurp(const std::string &path, bool &ok) {
    std::ifstream in(path, std::ios::binary);
    ok = in.good();
    if (!ok) return std::string();
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

inline size_t skipWs(const std::string &s, size_t i) {
    while (i < s.size() && (s[i] == ' ' || s[i] == '\n' || s[i] == '\t' || s[i] == '\r'))
        ++i;
    return i;
}

// Finds "key" at or after `from` and returns the index just past its colon.
inline size_t field(const std::string &s, const std::string &key, size_t from) {
    std::string pat = "\"" + key + "\"";
    size_t i = s.find(pat, from);
    if (i == std::string::npos) return std::string::npos;
    i = s.find(':', i + pat.size());
    if (i == std::string::npos) return std::string::npos;
    return skipWs(s, i + 1);
}

inline std::string readString(const std::string &s, size_t i) {
    if (i == std::string::npos || i >= s.size() || s[i] != '"') return std::string();
    std::string out;
    for (++i; i < s.size() && s[i] != '"'; ++i) {
        if (s[i] == '\\' && i + 1 < s.size()) ++i;
        out.push_back(s[i]);
    }
    return out;
}

inline double readNumber(const std::string &s, size_t i) {
    if (i == std::string::npos) return 0;
    return std::strtod(s.c_str() + i, nullptr);
}

inline std::vector<unsigned char> readIntArray(const std::string &s, size_t i) {
    std::vector<unsigned char> out;
    if (i == std::string::npos || i >= s.size() || s[i] != '[') return out;
    for (++i; i < s.size() && s[i] != ']'; ++i) {
        if (s[i] >= '0' && s[i] <= '9') {
            long v = std::strtol(s.c_str() + i, nullptr, 10);
            out.push_back((unsigned char)(v < 0 ? 0 : (v >= catCount ? catCold : v)));
            while (i < s.size() && s[i] >= '0' && s[i] <= '9') ++i;
            --i;
        }
    }
    return out;
}

}  // namespace detail

inline MapData loadMap(const std::string &path) {
    using namespace detail;
    MapData m;
    bool ok = false;
    std::string s = slurp(path, ok);
    if (!ok) {
        m.error = "cannot open " + path + " - run: python3 -m sim.bench";
        return m;
    }
    m.drive = readString(s, field(s, "drive", 0));
    m.clusters = (long)readNumber(s, field(s, "clusters", 0));
    m.clusterKb = (long)readNumber(s, field(s, "cluster_kb", 0));

    size_t at = s.find("\"layouts\"");
    if (at == std::string::npos) {
        m.error = "no layouts in " + path;
        return m;
    }
    while (true) {
        size_t nameAt = field(s, "name", at);
        if (nameAt == std::string::npos) break;
        Layout l;
        l.name = readString(s, nameAt);
        l.label = readString(s, field(s, "label", nameAt));
        l.cells = readIntArray(s, field(s, "cells", nameAt));
        l.bootMs = readNumber(s, field(s, "boot_ms", nameAt));
        l.wordMs = readNumber(s, field(s, "word_ms", nameAt));
        l.pagingMs = readNumber(s, field(s, "paging_ms", nameAt));
        l.dayMs = readNumber(s, field(s, "day_ms", nameAt));
        l.extentsPerFile = readNumber(s, field(s, "extents_per_file", nameAt));
        l.pctFragmented = readNumber(s, field(s, "pct_fragmented", nameAt));
        l.freeHoles = (long)readNumber(s, field(s, "free_holes", nameAt));
        if (l.cells.empty()) break;
        m.layouts.push_back(l);
        at = field(s, "free_holes", nameAt);
        if (at == std::string::npos) break;
    }
    if (m.layouts.empty()) m.error = "no usable layouts in " + path;
    return m;
}

}  // namespace defrag95

#endif

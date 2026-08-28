// defrag95 - a defragmenter for Windows 95 that organises by use, not by
// directory order.
//
// This is the front end: a Turbo Vision console application in the spirit of
// the tools that shipped with the machines it is about, rendered with
// magiblot's open-source Turbo Vision, so it gets true colour, Unicode and a
// mouse on a modern terminal.
//
// It reads results/clustermap.json, which the simulator in sim/ writes, and
// shows what each defragmenter does to the same volume and what that costs.
//
// Keith Adler

#define Uses_TApplication
#define Uses_TButton
#define Uses_TDeskTop
#define Uses_TDialog
#define Uses_TDrawBuffer
#define Uses_TEvent
#define Uses_TKeys
#define Uses_TMenuBar
#define Uses_TMenuItem
#define Uses_TRect
#define Uses_TScreen
#define Uses_TStaticText
#define Uses_TStatusDef
#define Uses_TStatusItem
#define Uses_TStatusLine
#define Uses_TSubMenu
#define Uses_TView
#define Uses_TWindow
#define Uses_MsgBox
#include <tvision/tv.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "mapdata.h"

using namespace defrag95;

const ushort cmAbout      = 1000;
const ushort cmLayoutNext = 1001;
const ushort cmLayoutPrev = 1002;
const ushort cmRunPass    = 1003;
const ushort cmRunMaint   = 1004;
const ushort cmCompare    = 1005;
const ushort cmFirstLayout = 1100;   // + index

static std::string gMapPath = "results/clustermap.json";

// --- palette -----------------------------------------------------------------

struct Swatch { TColorRGB fg; const char *glyph; const char *name; };

static const Swatch kSwatch[catCount] = {
    { TColorRGB(0x30, 0x34, 0x3c), "\xE2\x96\x91", "free"       },  // light shade
    { TColorRGB(0x6c, 0xd8, 0xff), "\xE2\x96\x88", "boot set"   },
    { TColorRGB(0x7a, 0xe5, 0x82), "\xE2\x96\x88", "app sets"   },
    { TColorRGB(0xff, 0xc6, 0x4d), "\xE2\x96\x88", "swap file"  },
    { TColorRGB(0xb7, 0x9c, 0xff), "\xE2\x96\x88", "warm"       },
    { TColorRGB(0xff, 0x8a, 0x3d), "\xE2\x96\x88", "churn"      },
    { TColorRGB(0x5a, 0x63, 0x74), "\xE2\x96\x93", "cold"       },
    { TColorRGB(0xff, 0x5f, 0x87), "\xE2\x96\x88", "fragmented" },
};

static const TColorRGB kBack(0x12, 0x14, 0x1a);
static const TColorRGB kInk(0xd8, 0xdd, 0xe6);
static const TColorRGB kDim(0x8a, 0x93, 0xa6);
static const TColorRGB kAccent(0x6c, 0xd8, 0xff);
static const TColorRGB kGood(0x7a, 0xe5, 0x82);
static const TColorRGB kBad(0xff, 0x8a, 0x3d);

static TColorAttr ink(TColorRGB fg) { return TColorAttr(fg, kBack); }

// --- the view ----------------------------------------------------------------

class DefragView : public TView {
public:
    DefragView(const TRect &bounds, MapData *data);
    virtual void draw() override;
    virtual void handleEvent(TEvent &event) override;
    void selectLayout(int index);
    void startPass(bool maintenance);
    bool animating() const { return anim >= 0; }
    void step();
    int layoutIndex() const { return current; }
    const MapData *map() const { return data; }

private:
    MapData *data;
    int current = 0;
    int anim = -1;                       // cells revealed so far, -1 = idle
    bool maintenance = false;
    std::vector<unsigned char> shown;    // what is on screen right now
    int mapRows = 0, mapCols = 0;

    void drawMap(int &y);
    void drawLegend(int &y);
    void drawStats(int &y);
    void line(int y, const std::string &text, TColorAttr attr);
    const Layout &layout(int i) const { return data->layouts[i]; }
    const Layout &baseline() const;
};

const Layout &DefragView::baseline() const {
    const Layout *l = data->find("none");
    return l ? *l : data->layouts[0];
}

DefragView::DefragView(const TRect &bounds, MapData *d) : TView(bounds), data(d) {
    options |= ofSelectable | ofFirstClick;
    eventMask |= evKeyDown | evMouseDown;
    growMode = gfGrowHiX | gfGrowHiY;
    if (data->ok()) shown = data->layouts[0].cells;
}

void DefragView::selectLayout(int index) {
    if (!data->ok()) return;
    if (index < 0 || index >= (int)data->layouts.size()) return;
    current = index;
    anim = -1;
    shown = layout(current).cells;
    drawView();
}

void DefragView::startPass(bool maint) {
    if (!data->ok()) return;
    maintenance = maint;
    shown = baseline().cells;
    anim = 0;
    drawView();
}

void DefragView::step() {
    if (anim < 0) return;
    const std::vector<unsigned char> &target = layout(current).cells;
    // A maintenance pass only rewrites what has drifted, so it sweeps faster.
    int chunk = std::max<int>(1, (int)target.size() / (maintenance ? 60 : 240));
    int end = std::min<int>((int)target.size(), anim + chunk);
    for (; anim < end; ++anim)
        if (anim < (int)shown.size()) shown[anim] = target[anim];
    if (anim >= (int)target.size()) anim = -1;
    drawView();
}

void DefragView::line(int y, const std::string &text, TColorAttr attr) {
    TDrawBuffer b;
    b.moveChar(0, ' ', ink(kInk), size.x);
    b.moveStr(0, TStringView(text.c_str(), (int)text.size()), attr);
    writeLine(0, y, size.x, 1, b);
}

void DefragView::draw() {
    TDrawBuffer b;
    b.moveChar(0, ' ', ink(kInk), size.x);
    for (int y = 0; y < size.y; ++y) writeLine(0, y, size.x, 1, b);

    if (!data->ok()) {
        line(1, " " + data->error, ink(kBad));
        line(3, "  The benchmark writes it:  python3 -m sim.bench", ink(kDim));
        return;
    }
    int y = 0;
    drawMap(y);
    drawLegend(y);
    drawStats(y);
}

void DefragView::drawMap(int &y) {
    const Layout &l = layout(current);
    char head[256];
    double gain = 0;
    const Layout *w95 = data->find("win95_full");
    if (w95 && w95->bootMs > 0)
        gain = (w95->bootMs - l.bootMs) / w95->bootMs * 100.0;
    std::snprintf(head, sizeof head, " %s   %ld clusters of %ld KB",
                  l.label.c_str(), data->clusters, data->clusterKb);
    line(y++, head, TColorAttr(kAccent, kBack));

    // rows below the map: legend, the boot-time table, and two summary lines
    int below = 3 + (int)data->layouts.size() + 1;
    mapCols = std::max(8, (int)size.x - 2);
    mapRows = std::max(3, (int)size.y - below - 1);
    long cells = (long)mapRows * mapCols;
    long total = (long)shown.size();

    for (int row = 0; row < mapRows; ++row) {
        TDrawBuffer b;
        b.moveChar(0, ' ', ink(kInk), size.x);
        for (int col = 0; col < mapCols; ++col) {
            // the whole volume always fills the whole grid, whichever is bigger
            long idx = (long)row * mapCols + col;
            long start = idx * total / cells;
            long stop = std::max(start + 1, (idx + 1) * total / cells);
            if (start >= total) break;
            // show the most interesting thing in the span, not the most common
            int best = catFree;
            for (long k = start; k < stop && k < total; ++k) {
                int c = shown[k];
                if (c == catFragmented) { best = c; break; }
                if (c != catFree) best = (best == catFree) ? c : std::min(best, c);
            }
            b.moveStr(1 + col, TStringView(kSwatch[best].glyph),
                      TColorAttr(kSwatch[best].fg, kBack));
        }
        writeLine(0, y + row, size.x, 1, b);
    }
    y += mapRows;
    if (anim >= 0) {
        int pct = (int)(100.0 * anim / std::max<size_t>(1, layout(current).cells.size()));
        char buf[128];
        std::snprintf(buf, sizeof buf, " %s pass: %d%% - relocating clusters",
                      maintenance ? "Maintenance" : "Full", pct);
        line(y++, buf, TColorAttr(kGood, kBack));
    } else {
        char buf[160];
        std::snprintf(buf, sizeof buf,
                      " %.2f extents/file   %.1f%% of files fragmented   %ld free-space holes",
                      l.extentsPerFile, l.pctFragmented, l.freeHoles);
        line(y++, buf, ink(kDim));
    }
}

void DefragView::drawLegend(int &y) {
    TDrawBuffer b;
    b.moveChar(0, ' ', ink(kInk), size.x);
    int x = 1;
    for (int i = 0; i < catCount; ++i) {
        b.moveStr(x, TStringView(kSwatch[i].glyph), TColorAttr(kSwatch[i].fg, kBack));
        x += 2;
        std::string n = kSwatch[i].name;
        b.moveStr(x, TStringView(n.c_str(), (int)n.size()), ink(kDim));
        x += (int)n.size() + 2;
        if (x > size.x - 12) break;
    }
    writeLine(0, y++, size.x, 1, b);
}

void DefragView::drawStats(int &y) {
    const Layout *w95 = data->find("win95_full");
    double worst = 0;
    for (const auto &l : data->layouts) worst = std::max(worst, l.bootMs);

    line(y++, " Cold boot, disk time                                     (held-out workload)",
         ink(kInk));
    for (size_t i = 0; i < data->layouts.size() && y < size.y; ++i) {
        const Layout &l = data->layouts[i];
        int barMax = std::max(10, size.x - 46 - 10);   // leave room for the % label
        int width = worst > 0 ? (int)(barMax * l.bootMs / worst) : 0;
        TDrawBuffer b;
        b.moveChar(0, ' ', ink(kInk), size.x);
        bool sel = (int)i == current;
        std::string mark = sel ? " > " : "   ";
        b.moveStr(0, TStringView(mark.c_str(), 3), TColorAttr(kAccent, kBack));
        std::string label = l.label;
        if ((int)label.size() > 32) label = label.substr(0, 32);
        b.moveStr(3, TStringView(label.c_str(), (int)label.size()),
                  sel ? TColorAttr(kInk, kBack) : ink(kDim));
        char num[64];
        std::snprintf(num, sizeof num, "%7.0f ms", l.bootMs);
        b.moveStr(36, TStringView(num, (int)std::strlen(num)),
                  sel ? TColorAttr(kAccent, kBack) : ink(kDim));
        TColorRGB barColor = (l.name == "defrag95") ? kGood : kDim;
        for (int k = 0; k < width && 46 + k < size.x; ++k)
            b.moveStr(46 + k, TStringView("\xE2\x96\x86"), TColorAttr(barColor, kBack));
        if (w95 && w95->bootMs > 0 && l.name != "win95_full") {
            char pct[32];
            std::snprintf(pct, sizeof pct, " %+.1f%%",
                          (w95->bootMs - l.bootMs) / w95->bootMs * 100.0);
            int at = std::min<int>(size.x - 8, 47 + width);
            b.moveStr(at, TStringView(pct, (int)std::strlen(pct)),
                      TColorAttr(l.bootMs < w95->bootMs ? kGood : kBad, kBack));
        }
        writeLine(0, y++, size.x, 1, b);
    }
    if (y < size.y) {
        const Layout &l = layout(current);
        char buf[200];
        std::snprintf(buf, sizeof buf,
                      "   Launch Word %.0f ms    Paging storm %.0f ms    Working day %.1f s",
                      l.wordMs, l.pagingMs, l.dayMs / 1000.0);
        line(y++, buf, ink(kDim));
    }
}

void DefragView::handleEvent(TEvent &event) {
    TView::handleEvent(event);
    if (event.what == evKeyDown) {
        switch (event.keyDown.keyCode) {
            case kbTab:
            case kbRight:
            case kbDown:
                message(owner, evCommand, cmLayoutNext, nullptr);
                clearEvent(event);
                return;
            case kbShiftTab:
            case kbLeft:
            case kbUp:
                message(owner, evCommand, cmLayoutPrev, nullptr);
                clearEvent(event);
                return;
            default:
                break;
        }
        char c = event.keyDown.charScan.charCode;
        if (c >= '1' && c <= '9') {
            selectLayout(c - '1');
            clearEvent(event);
        }
    }
}

// --- window ------------------------------------------------------------------

class DefragWindow : public TWindow {
public:
    DefragWindow(const TRect &bounds, MapData *data);
    DefragView *view;
};

DefragWindow::DefragWindow(const TRect &bounds, MapData *data)
    : TWindowInit(&DefragWindow::initFrame),
      TWindow(bounds, "defrag95  -  Keith Adler", wnNoNumber) {
    options |= ofTileable;
    flags &= ~(wfClose);
    TRect r = getExtent();
    r.grow(-1, -1);
    view = new DefragView(r, data);
    insert(view);
}

// --- application --------------------------------------------------------------

class Defrag95App : public TApplication {
public:
    Defrag95App();
    static TMenuBar *initMenuBar(TRect r);
    static TStatusLine *initStatusLine(TRect r);
    virtual void handleEvent(TEvent &event) override;
    virtual void idle() override;

private:
    MapData data;
    DefragWindow *win = nullptr;
    int ticks = 0;
    void about();
};

Defrag95App::Defrag95App()
    : TProgInit(&Defrag95App::initStatusLine, &Defrag95App::initMenuBar,
                &Defrag95App::initDeskTop) {
    data = loadMap(gMapPath);
    TRect r = deskTop->getExtent();
    win = new DefragWindow(r, &data);
    deskTop->insert(win);
}

TMenuBar *Defrag95App::initMenuBar(TRect r) {
    r.b.y = r.a.y + 1;
    return new TMenuBar(
        r,
        *new TSubMenu("~D~isk", kbAltD) +
            *new TMenuItem("~F~ull pass", cmRunPass, kbF9, hcNoContext, "F9") +
            *new TMenuItem("~M~aintenance pass", cmRunMaint, kbF10, hcNoContext, "F10") +
            newLine() +
            *new TMenuItem("E~x~it", cmQuit, kbAltX, hcNoContext, "Alt-X") +
        *new TSubMenu("~L~ayout", kbAltL) +
            *new TMenuItem("~1~  No defrag", cmFirstLayout + 0, kbNoKey) +
            *new TMenuItem("~2~  Win95 Defrag, files only", cmFirstLayout + 1, kbNoKey) +
            *new TMenuItem("~3~  Win95 Defrag, full", cmFirstLayout + 2, kbNoKey) +
            *new TMenuItem("~4~  Win95 Defrag, DOS mode", cmFirstLayout + 3, kbNoKey) +
            *new TMenuItem("~5~  defrag95", cmFirstLayout + 4, kbNoKey) +
            newLine() +
            *new TMenuItem("~N~ext", cmLayoutNext, kbTab, hcNoContext, "Tab") +
        *new TSubMenu("~H~elp", kbAltH) +
            *new TMenuItem("~A~bout defrag95", cmAbout, kbF1, hcNoContext, "F1"));
}

TStatusLine *Defrag95App::initStatusLine(TRect r) {
    r.a.y = r.b.y - 1;
    return new TStatusLine(
        r,
        *new TStatusDef(0, 0xFFFF) +
            *new TStatusItem("~F1~ About", kbF1, cmAbout) +
            *new TStatusItem("~Tab~ Layout", kbTab, cmLayoutNext) +
            *new TStatusItem("~F9~ Full pass", kbF9, cmRunPass) +
            *new TStatusItem("~F10~ Maintenance", kbF10, cmRunMaint) +
            *new TStatusItem("~Alt-X~ Exit", kbAltX, cmQuit) +
            *new TStatusItem(0, kbCtrlC, cmQuit));
}

void Defrag95App::about() {
    TDialog *d = new TDialog(TRect(0, 0, 60, 17), "About defrag95");
    d->options |= ofCentered;
    d->insert(new TStaticText(TRect(3, 2, 57, 4),
        "\003defrag95\n\003a defragmenter that organises by use"));
    d->insert(new TStaticText(TRect(3, 5, 57, 12),
        "Windows 95 packed files against the front of the volume "
        "in directory order. That removes fragmentation but leaves "
        "the boot set scattered across everything it was installed "
        "beside. defrag95 lays the disk out in the order the machine "
        "actually reads it, and keeps churn out of the way."));
    d->insert(new TStaticText(TRect(3, 12, 57, 14),
        "\003Keith Adler\n\003Turbo Vision UI on magiblot/tvision"));
    d->insert(new TButton(TRect(24, 14, 36, 16), "~O~K", cmOK, bfDefault));
    executeDialog(d);
}

void Defrag95App::handleEvent(TEvent &event) {
    TApplication::handleEvent(event);
    if (event.what != evCommand) return;
    DefragView *v = win ? win->view : nullptr;
    switch (event.message.command) {
        case cmAbout:
            about();
            break;
        case cmRunPass:
            if (v) v->startPass(false);
            break;
        case cmRunMaint:
            if (v) v->startPass(true);
            break;
        case cmLayoutNext:
            if (v && v->map()->ok())
                v->selectLayout((v->layoutIndex() + 1) % (int)v->map()->layouts.size());
            break;
        case cmLayoutPrev:
            if (v && v->map()->ok()) {
                int n = (int)v->map()->layouts.size();
                v->selectLayout((v->layoutIndex() + n - 1) % n);
            }
            break;
        default:
            if (event.message.command >= cmFirstLayout &&
                event.message.command < cmFirstLayout + 16) {
                if (v) v->selectLayout(event.message.command - cmFirstLayout);
            } else {
                return;
            }
    }
    clearEvent(event);
}

void Defrag95App::idle() {
    TApplication::idle();
    if (win && win->view && win->view->animating() && ++ticks % 2 == 0)
        win->view->step();
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--help" || a == "-h") {
            std::printf("defrag95 - Keith Adler\n"
                        "usage: defrag95 [path/to/clustermap.json]\n"
                        "  defaults to results/clustermap.json, written by "
                        "python3 -m sim.bench\n");
            return 0;
        }
        gMapPath = a;
    }
    Defrag95App app;
    app.run();
    return 0;
}

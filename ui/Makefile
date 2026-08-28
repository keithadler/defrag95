# defrag95 UI - Turbo Vision front end
#
# Builds against magiblot's Turbo Vision. If you do not have it, `make deps`
# fetches it into third_party/ and builds a static library with no dependency
# on CMake; only a C++17 compiler and ncurses are needed.
#
# Keith Adler

CXX      ?= c++
CXXFLAGS ?= -std=c++17 -O2
TVISION  ?= third_party/tvision
TVLIB    := $(TVISION)/build/libtvision.a
INCLUDES := -I$(TVISION)/include -Isrc
LDLIBS   := -lncurses
BIN      := defrag95

TVFLAGS := -std=c++17 -O2 -w -DHAVE_NCURSES \
           -I$(TVISION)/include \
           -I$(TVISION)/include/tvision \
           -I$(TVISION)/include/tvision/compat/borland \
           -I$(TVISION)/include/tvision/compat/malloc \
           -I$(TVISION)/source

.PHONY: all deps clean run

all: $(BIN)

$(BIN): src/defrag95.cpp src/mapdata.h $(TVLIB)
	$(CXX) $(CXXFLAGS) $(INCLUDES) src/defrag95.cpp -o $@ $(TVLIB) $(LDLIBS)

deps: $(TVLIB)

$(TVISION)/CMakeLists.txt:
	mkdir -p third_party
	git clone --depth 1 https://github.com/magiblot/tvision.git $(TVISION)

$(TVLIB): $(TVISION)/CMakeLists.txt
	mkdir -p $(TVISION)/build/obj
	@for f in $(TVISION)/source/tvision/*.cpp $(TVISION)/source/platform/*.cpp; do \
	    case "$$f" in *geninc.cpp) continue;; esac; \
	    o=$(TVISION)/build/obj/`basename $${f%.cpp}`.o; \
	    [ -f "$$o" ] || $(CXX) $(TVFLAGS) -c "$$f" -o "$$o" || exit 1; \
	done
	ar rcs $@ $(TVISION)/build/obj/*.o

run: $(BIN)
	cd .. && ui/$(BIN)

clean:
	rm -f $(BIN)

distclean: clean
	rm -rf third_party

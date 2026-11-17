#import <UIKit/UIKit.h>
#include <stdbool.h>
void calliope_ios_start(const char *files);
void calliope_ios_active(bool active);
void *gpui_ios_get_window(void);
void *gpui_ios_view_controller(void *window);
void gpui_ios_layout_view(void *window);
bool gpui_ios_request_frame(void *window);

#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_advanced_gtk» - Dell Recovery Media Builder
#
# Copyright (C) 2010, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>
#
# This is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this application; if not, write to the Free Software Foundation, Inc., 51
# Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
##################################################################################

import dbus
import os
import sys
from gi.repository import Gtk
import subprocess
import datetime

from debian import debian_support
from Dell.recovery_gtk import DellRecoveryToolGTK, translate_widgets
from Dell.recovery_basic_gtk import BasicGeneratorGTK

from Dell.recovery_common import (UIDIR, UP_FILENAMES,
                                  dbus_sync_call_signal_wrapper,
                                  find_burners,
                                  increment_bto_version)

#TODO: aptdaemon needs gtk3 support
#try:
#    from aptdaemon import client
#    from aptdaemon.gtkwidgets import AptProgressDialog
#except ImportError:
#    pass

#Translation support
from gettext import gettext as _

class AdvancedGeneratorGTK(BasicGeneratorGTK):
    """The AdvancedGeneratorGTK is the GTK generator that can generate recovery
       images from a variety of dynamic contents, including the recovery
       partition, drivers, applications, isos, and more.
    """
    def __init__(self, utility, recovery, version, media, target,
                 overwrite, xrev, branch):
        """Inserts builder widgets into the Gtk.Assistant"""

        #Run the normal init first
        BasicGeneratorGTK.__init__(self, utility, recovery,
                                   version, media, target, overwrite)

        #Build our extra GUI in
        self.builder_widgets = Gtk.Builder()
        self.builder_widgets.add_from_file(os.path.join(UIDIR, 'builder.ui'))
        self.builder_widgets.connect_signals(self)

        translate_widgets(self.builder_widgets)

        wizard = self.widgets.get_object('wizard')
        #wizard.resize(400,400)
        wizard.set_title(wizard.get_title() + _(" (BTO Image Builder Mode)"))

        self.tool_widgets.get_object('build_os_media_label').set_text(_("This \
will integrate a Dell \
OEM FID framework & driver package set into a customized \
OS media image.  You will have the option to \
create an USB key or DVD image."))

        self.file_dialog = Gtk.FileChooserDialog("Choose Item",
                                        None,
                                        Gtk.FileChooserAction.OPEN,
                                        (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        self.file_dialog.set_default_response(Gtk.ResponseType.OK)

        #setup transient windows
        for window in ['builder_add_dell_recovery_window',
                       'srv_dialog']:
            self.builder_widgets.get_object(window).set_transient_for(wizard)

        #insert builder pages in reverse order
        titles = {'oie_page' : _("Installation Mode"),
                 'application_page' : _("Choose Application Packages"),
                 'driver_page' : _("Choose Driver Packages"),
                 'up_page' : _("Choose Utility Partition"),
                 'fid_page' : _("Choose FID Overlay"),
                 'base_page' : _("Choose Base OS Image")
                }
        for page in ['oie_page',
                     'application_page',
                     'driver_page',
                     'up_page',
                     'fid_page',
                     'base_page']:
            wizard.insert_page(self.builder_widgets.get_object(page), 0)
            wizard.set_page_title(wizard.get_nth_page(0), titles[page])

        #improve the summary
        self.widgets.get_object('version_hbox').show()

        #builder variable defaults
        self.xrev = xrev
        self.branch = branch
        self.builder_base_image = ''
        self.bto_base = False
        self.bto_up = ''
        self.add_dell_recovery_deb = ''
        self.oie_mode = False
        self.success_script = ''
        self.fail_script = ''
        self.apt_client = None

        self.builder_widgets.connect_signals(self)

    def top_button_clicked(self, widget):
        """Overridden method to make us generate OS media"""
        #hide the main page
        if DellRecoveryToolGTK.top_button_clicked(self, widget):
            #show our page
            self.widgets.get_object('wizard').show()
    
            self.tool_widgets.get_object('tool_selector').hide()

    def build_page(self, widget, page = None):
        """Processes output that should be done on a builder page"""
        #Do the normal processing first
        BasicGeneratorGTK.build_page(self, widget, page)

        wizard = self.widgets.get_object('wizard')
        if page == self.builder_widgets.get_object('base_page'):
            if self.rp:
                self.builder_widgets.get_object('recovery_hbox').set_sensitive(True)
            file_filter = Gtk.FileFilter()
            file_filter.add_pattern("*.iso")
            self.file_dialog.set_filter(file_filter)

        elif page == self.builder_widgets.get_object('fid_page'):
            self.fid_toggled(None)

        elif page == self.builder_widgets.get_object('up_page'):
            if self.up:
                self.builder_widgets.get_object('utility_hbox').set_sensitive(True)
            file_filter = Gtk.FileFilter()
            for item in UP_FILENAMES:
                pattern = item.split('.')[1]
                file_filter.add_pattern("*.%s" % pattern)

            self.file_dialog.set_filter(file_filter)
            self.up_toggled(None)

        elif page == self.builder_widgets.get_object('driver_page'):
            self.file_dialog.set_action(Gtk.FileChooserAction.OPEN)
            filefilter = Gtk.FileFilter()
            filefilter.add_pattern("*.tgz")
            filefilter.add_pattern("*.tar.gz")
            filefilter.add_pattern("*.deb")
            filefilter.add_pattern("*.pdf")
            filefilter.add_pattern("*.py")
            filefilter.add_pattern("*.sh")

            self.file_dialog.set_filter(filefilter)
            wizard.set_page_complete(page, True)

        elif page == self.builder_widgets.get_object('application_page'):
            self.file_dialog.set_action(Gtk.FileChooserAction.OPEN)
            filefilter = Gtk.FileFilter()
            filefilter.add_pattern("*.tgz")
            filefilter.add_pattern("*.tar.gz")
            filefilter.add_pattern("*.zip")

            self.file_dialog.set_filter(filefilter)
            self.calculate_srvs(None, -1, "check")

        elif page == self.builder_widgets.get_object('oie_page'):
            filefilter = Gtk.FileFilter()
            filefilter.add_pattern('*')
            self.file_dialog.set_filter(filefilter)
            wizard.set_page_complete(page, True)

        elif page == self.widgets.get_object('conf_page') or \
             widget == self.widgets.get_object('version'):

            if page:
                wizard.set_page_title(page, _("Builder Summary"))
            output_text  = "<b>" + _("Base Image Distributor") + "</b>: "
            output_text += self.distributor + '\n'
            output_text += "<b>" + _("Base Image Release") + "</b>: "
            output_text += self.release + '\n'
            if self.bto_base:
                output_text += "<b>" + _("BTO Base Image") + "</b>: "
                output_text += self.builder_base_image + '\n'
            else:
                output_text += "<b>" + _("Base Image") + "</b>: "
                output_text += self.builder_base_image + '\n'

            if self.bto_up:
                output_text +="<b>" + _("Utility Partition: ") + '</b>'
                output_text += self.bto_up + '\n'

            liststores = {'application_liststore' : _("Application"),
                          'driver_liststore' : _("Driver"),
                         } 
            for item in liststores:
                model = self.builder_widgets.get_object(item)
                iterator = model.get_iter_first()
                if iterator is not None:
                    output_text += "<b>%s %s</b>:\n" % (liststores[item], _("Packages"))
                while iterator is not None:
                    output_text += "\t" + model.get_value(iterator, 0) + '\n'
                    iterator = model.iter_next(iterator)

            if self.add_dell_recovery_deb:
                output_text += "<b>" + _("Inject Dell Recovery Package") + "</b>: "
                output_text += self.add_dell_recovery_deb + '\n'

            output_text += "<b>" + _("OIE Installation Mode") + "</b>: "
            output_text += str(self.oie_mode) + '\n'
            if self.success_script:
                output_text += "<b>" + _("Additional Success Script") + "</b>: "
                output_text += self.success_script + '\n'
            if self.success_script:
                output_text += "<b>" + _("Additional Fail Script") + "</b>: "
                output_text += self.fail_script + '\n'

            output_text += self.widgets.get_object('conf_text').get_label()

            self.widgets.get_object('conf_text').set_markup(output_text)

    def wizard_complete(self, widget, function = None, args = None):
        """Finished answering wizard questions, and can continue process"""
        #update gui
        self.widgets.get_object('action').set_text(_('Assembling Image Components'))

        #build driver list
        driver_fish_list = []
        model = self.builder_widgets.get_object('driver_liststore')
        iterator = model.get_iter_first()
        while iterator is not None:
            driver_fish_list.append(model.get_value(iterator, 0))
            iterator = model.iter_next(iterator)

        #build application list
        application_fish_list = {}
        model = self.builder_widgets.get_object('application_liststore')
        iterator = model.get_iter_first()
        while iterator is not None:
            path = model.get_value(iterator, 0)
            srv = model.get_value(iterator, 1)
            application_fish_list[path] = srv
            iterator = model.iter_next(iterator)

        if self.oie_mode:
            build = 0
            while True:
                path = os.path.join(self.path, 'oie-%s-%i' % (datetime.date.today(), build))
                if not os.path.exists(path):
                    self.path = path
                    break
                build = build + 1
            self.image = '%s-%s-%s-dell-oie_%s.tar' % (self.distributor,
                                                          self.release,
                                                          self.arch,
                              self.widgets.get_object('version').get_text())

        function = 'assemble_image'
        args = (self.builder_base_image,
                driver_fish_list,
                application_fish_list,
                self.add_dell_recovery_deb,
                self.oie_mode,
                self.success_script,
                self.fail_script,
                'create_' + self.distributor,
                self.bto_up)

        BasicGeneratorGTK.wizard_complete(self, widget, function, args)

    def run_file_dialog(self):
        """Browses all files under a particular filter"""
        response = self.file_dialog.run()
        self.file_dialog.hide()
        if response == Gtk.ResponseType.OK:
            return self.file_dialog.get_filename()
        else:
            return None

    def up_toggled(self, widget):
        """Called when the radio button for the Builder utility partition page
           is changed"""
        up_browse_button = self.builder_widgets.get_object('up_browse_button')
        up_page = self.builder_widgets.get_object('up_page')
        wizard = self.widgets.get_object('wizard')

        if self.builder_widgets.get_object('up_files_radio').get_active():
            self.builder_widgets.get_object('up_details_label').set_markup("")
            self.file_dialog.set_action(Gtk.FileChooserAction.OPEN)
            up_browse_button.set_sensitive(True)
            wizard.set_page_complete(up_page, False)
        else:
            if self.builder_widgets.get_object('up_partition_radio').get_active():
                self.bto_up = self.up
            else:
                self.bto_up = ''
            wizard.set_page_complete(up_page, True)
            up_browse_button.set_sensitive(False)
            self.up_file_chooser_picked()

    def up_file_chooser_picked(self, widget=None):
        """Called when a file is selected on the up page"""

        up_page = self.builder_widgets.get_object('up_page')
        wizard = self.widgets.get_object('wizard')

        if widget == self.builder_widgets.get_object('up_browse_button'):
            ret = self.run_file_dialog()
            if ret is not None:
                self.bto_up = ret
                wizard.set_page_complete(up_page, True)

        if self.bto_up:
            call = subprocess.Popen(['file', self.bto_up],
                                    stdout=subprocess.PIPE)
            output_text  = "<b>" + _("Utility Partition") + "</b>:\n"
            output_text += call.communicate()[0].replace(', ', '\n')
        else:
            output_text  = _("No Additional Utility Partition")

        self.builder_widgets.get_object('up_details_label').set_markup(output_text)

    def base_toggled(self, widget):
        """Called when the radio button for the Builder base image page is
           changed"""
        base_browse_button = self.builder_widgets.get_object('base_browse_button')
        base_page = self.builder_widgets.get_object('base_page')
        wizard = self.widgets.get_object('wizard')
        label = self.builder_widgets.get_object('base_image_details_label')

        label.set_markup("")
        base_browse_button.set_sensitive(True)
        wizard.set_page_complete(base_page, False)

        if self.builder_widgets.get_object('iso_image_radio').get_active():
            self.file_dialog.set_action(Gtk.FileChooserAction.OPEN)
        elif self.builder_widgets.get_object('directory_radio').get_active():
            self.file_dialog.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
        else:
            base_browse_button.set_sensitive(False)
            self.base_file_chooser_picked()

    def base_file_chooser_picked(self, widget=None):
        """Called when a file is selected on the base page"""

        base_page = self.builder_widgets.get_object('base_page')
        wizard = self.widgets.get_object('wizard')
        wizard.set_page_complete(base_page, False)

        bto_version = ''
        output_text = ''
        distributor = ''
        release = ''
        arch = ''
        if widget == self.builder_widgets.get_object('base_browse_button'):
            ret = self.run_file_dialog()
        else:
            ret = self.rp
        
        if ret is not None:
            self.toggle_spinner_popup(True)
            try:
                dbus_sync_call_signal_wrapper(self.backend(),
                                            'query_iso_information',
                                            {'report_iso_info': self.update_version_gui},
                                            ret)
            except dbus.DBusException, msg:
                parent = self.widgets.get_object('wizard')
                self.dbus_exception_handler(msg, parent)
            self.toggle_spinner_popup(False)
            self.builder_base_image = ret

    def fid_toggled(self, widget):
        """Called when the radio button for the Builder FID overlay page is changed"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        label = self.builder_widgets.get_object('fid_overlay_details_label')

        wizard.set_page_complete(fid_page, False)
        self.builder_widgets.get_object('add_dell_recovery_button').set_sensitive(False)

        if self.builder_widgets.get_object('builtin_radio').get_active():
            wizard.set_page_complete(fid_page, True)
            label.set_markup("<b>Builtin</b>: BTO Compatible Image")
            self.add_dell_recovery_deb = ''

        elif self.builder_widgets.get_object('deb_radio').get_active():
            if self.add_dell_recovery_deb:
                wizard.set_page_complete(fid_page, True)
            else:
                label.set_markup("")
            self.builder_widgets.get_object('add_dell_recovery_button').set_sensitive(True)

    def fid_deb_changed(self, widget):
        """Detects the version of a newly found deb"""
        wizard = self.widgets.get_object('wizard')
        fid_page = self.builder_widgets.get_object('fid_page')
        wizard.set_page_complete(fid_page, False)
        if self.add_dell_recovery_deb:
            wizard.set_page_complete(fid_page, True)
            if self.add_dell_recovery_deb == 'dpkg-repack':
                output_text = "<b>Build</b> from on-system install"
            else:
                output_text = "<b>Add</b> from %s" % self.add_dell_recovery_deb
            self.builder_widgets.get_object('fid_overlay_details_label').set_markup(output_text)

    def driver_action(self, widget):
        """Called when the add or remove buttons are pressed on the driver action page"""
        add_button = self.builder_widgets.get_object('driver_add')
        remove_button = self.builder_widgets.get_object('driver_remove')
        treeview = self.builder_widgets.get_object('driver_treeview')
        model = treeview.get_model()
        if widget == add_button:
            ret = self.run_file_dialog()
            if ret is not None:
                #test that we don't have a file named identically
                if self.test_liststore_for_existing(model, ret):
                    return
                model.append([ret])
        elif widget == remove_button:
            row = treeview.get_selection().get_selected_rows()[1]
            if len(row) > 0:
                model.remove(model.get_iter(row[0]))

    def test_liststore_for_existing(self, model, test):
        """Tests the first column of a list store for the same content"""
        iterator = model.get_iter_first()
        while iterator is not None:
            iteration_text = model.get_value(iterator, 0)
            if iteration_text == test:
                return True
            iterator = model.iter_next(iterator)
        return False

    def application_action(self, widget):
        """Called when the add or remove buttons are pressed on the driver action page"""
        
        def run_srv_dialog():
            """Runs the SRV dialog"""
            srv_dialog = self.builder_widgets.get_object('srv_dialog')
            srv_entry = self.builder_widgets.get_object('srv_entry')
            wizard = self.widgets.get_object('wizard')
            srv_entry.set_text('')
            wizard.set_sensitive(False)
            srv_dialog.run()
            wizard.set_sensitive(True)
            srv_dialog.hide()
            srv = srv_entry.get_text().lower()
            #double check that it's not a duplicate
            if self.calculate_srvs(None, -1, srv):
                return srv
            return ""

        add_button = self.builder_widgets.get_object('application_add')
        remove_button = self.builder_widgets.get_object('application_remove')
        treeview = self.builder_widgets.get_object('application_treeview')

        model = treeview.get_model()
        if widget == add_button:
            file_ret = self.run_file_dialog()
            if file_ret is not None:
                #test that we don't have a file named identically
                if self.test_liststore_for_existing(model, file_ret):
                    return
                #query SRVs
                srv = run_srv_dialog()
                #append for reals
                model.append([file_ret, srv])
        elif widget == remove_button:
            row = treeview.get_selection().get_selected_rows()[1]
            if len(row) > 0:
                model.remove(model.get_iter(row[0]))
            self.calculate_srvs(None, -1, "check")

    def calculate_srvs(self, widget, path, text):
        """Verifies that no empty SRVs were defined"""
        wizard = self.widgets.get_object('wizard')
        page = self.builder_widgets.get_object('application_page')
        model = self.builder_widgets.get_object('application_liststore')
        warning = self.builder_widgets.get_object('srv_warning_label')

        #ONLY ever work from lowercase
        text = text.lower()

        #if we are adding text, check all SRVs in the treeview
        # * for duplicates
        # * for having content
        if text:
            proceed = True
            iterator = model.get_iter_first()
            while iterator is not None:
                if str(model.get_path(iterator)[0]) != path:
                    iteration_text = model.get_value(iterator, 1)
                    if not iteration_text:
                        proceed = False
                        break
                    if text == iteration_text:
                        proceed = False
                        text = ''
                        break
                iterator = model.iter_next(iterator)
        else:
            proceed = False

        #if we were editing the treeview (not the popup)
        #then add it to the list store
        if path >= 0:
            iterator = model.get_iter(path)
            model.set(iterator, 1, text)

        #Now that we've checked all SRVs, check showing warning and go forward
        if proceed:
            warning.set_text("")
        else:
            warning.set_text(_("All SRVs must be filled to proceed."))
        wizard.set_page_complete(page, proceed)
        return proceed

    def oie_toggled(self, widget):
        """The OIE radio was toggled. Only called back by active radio"""
        self.oie_mode = widget.get_active()
        
        if not widget.get_active():
            for label in ['success_label', 'fail_label']:
                obj = self.builder_widgets.get_object('success_label')
                obj.set_text('')
            self.success_script = ''
            self.fail_script = ''
            (self.cd_burn_cmd, self.usb_burn_cmd) = find_burners()
        else:
            self.cd_burn_cmd = None
            self.usb_burn_cmd = None

        file = self.builder_widgets.get_object('oie_file_chooser_vbox')
        file.set_sensitive(widget.get_active())

    def oie_file_chooser_clicked(self, widget):
        """The OIE file chooser was pressed"""
        ret = self.run_file_dialog()
        if ret is not None:
            if widget == self.builder_widgets.get_object('set_success_script'):
                self.success_script = ret
                label = self.builder_widgets.get_object('success_label')
                label.set_markup('<b>' + _("SUCCESS Script: " ) + '</b>' + ret)
            else:
                self.fail_script = ret
                label = self.builder_widgets.get_object('fail_label')
                label.set_markup('<b>' + _("FAIL Script: " ) + '</b>' + ret)

    def install_app(self, widget):
        """Launch into an installer for dpkg-repack"""
        packages = []
        wizard = self.widgets.get_object('wizard')
        
        packages = ['dpkg-repack']
        try:
            trans = self.apt_client.install_packages(packages,
                                    wait=False,
                                    reply_handler=None,
                                    error_handler=None)
            
            dialog = AptProgressDialog(trans, parent=wizard)
            dialog.run()
            super(AptProgressDialog, dialog).run()
        except dbus.exceptions.DBusException, msg:
            self.dbus_exception_handler(msg, wizard)

        widget.hide()

    def add_dell_recovery_clicked(self, widget):
        """Callback to launch a dialog to add dell-recovery to the image"""
        #check if dpkg-repack is available
        if not os.path.exists('/usr/bin/dpkg-repack'):
            if not self.apt_client:
                try:
                    self.apt_client = client.AptClient()
                except NameError:
                    pass
            if self.apt_client:
                self.builder_widgets.get_object('add_dell_recovery_repack_button').show()
            self.builder_widgets.get_object('build_dell_recovery_button').set_sensitive(False)
        else:
            self.builder_widgets.get_object('build_dell_recovery_button').set_sensitive(True)
        self.builder_widgets.get_object('builder_add_dell_recovery_window').show()

    def add_dell_recovery_closed(self, widget):
        """Callback for when the popup window to add dell-recovery is closed"""
        ok_button = self.builder_widgets.get_object('builder_add_ok')
        fid_page  = self.builder_widgets.get_object('fid_page')
        wizard = self.widgets.get_object('wizard')
        window = self.builder_widgets.get_object('builder_add_dell_recovery_window')

        if widget == ok_button:
            wizard.set_page_complete(fid_page, True)
        else:
            wizard.set_page_complete(fid_page, False)
            self.add_dell_recovery_deb = ''
        window.hide()

        #validate version
        if self.builder_widgets.get_object('deb_radio').get_active():
            self.fid_deb_changed(None)

    def add_dell_recovery_toggled(self, widget):
        """Toggles the active selection in the add dell-recovery to image page"""
        build_radio = self.builder_widgets.get_object('build_dell_recovery_button')
        browse_radio = self.builder_widgets.get_object('provide_dell_recovery_button')
        browse_button = self.builder_widgets.get_object('provide_dell_recovery_browse_button')
        ok_button = self.builder_widgets.get_object('builder_add_ok')

        if build_radio.get_active():
            ok_button.set_sensitive(True)
            browse_button.set_sensitive(False)
            self.add_dell_recovery_deb = 'dpkg-repack'
        elif browse_radio.get_active():
            ok_button.set_sensitive(False)
            browse_button.set_sensitive(True)
            self.add_dell_recovery_deb = ''

    def provide_dell_recovery_file_chooser_picked(self, widget=None):
        """Called when a file is selected on the add dell-recovery page"""

        ok_button = self.builder_widgets.get_object('builder_add_ok')
        filefilter = Gtk.FileFilter()
        filefilter.add_pattern("*.deb")
        self.file_dialog.set_filter(filefilter)
            
        ret = self.run_file_dialog()
        if ret is not None:
            import apt_inst
            import apt_pkg
            control = apt_inst.debExtractControl(open(ret))
            sections = apt_pkg.TagSection(control)
            if sections["Package"] != 'dell-recovery' or \
                                              debian_support.Version(sections["Version"]) < 0.72:
                self.add_dell_recovery_deb = ''
            else:
                self.add_dell_recovery_deb = ret

        if self.add_dell_recovery_deb:
            ok_button.set_sensitive(True)

    def update_version_gui(self, version, distributor, release, arch, output_text):
        """Stops any running spinners and updates GUI items"""
        BasicGeneratorGTK.update_version_gui(self, version, distributor, release, arch, output_text)

        if output_text:
            base_page = self.builder_widgets.get_object('base_page')
            wizard = self.widgets.get_object('wizard')
            wizard.set_page_complete(base_page, True)
            #If this is a BTO image, then allow using built in framework
            if not self.bto_base and self.builder_widgets.get_object('builtin_radio').get_active():
                self.builder_widgets.get_object('deb_radio').set_active(True)
        self.builder_widgets.get_object('builtin_hbox').set_sensitive(self.bto_base)

        self.builder_widgets.get_object('base_image_details_label').set_markup(output_text)

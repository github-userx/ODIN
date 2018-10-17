#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""
This module works like a Python version of FOCA. It can be used to search Google for files
under a provided domain and then download and extract metadata from the discovered files.
"""

import os
import re
import time
import zipfile
import subprocess

import lxml
import click
import requests
from PyPDF2 import PdfFileReader
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

from lib import helpers


class Metaparser:
    """Search a domain for files and then attempt to extract metadata from any discovered files."""
    def __init__(self, domain_name, page_results, exts, download_dir, webdriver):
        """Everything that should be initiated with a new object goes here."""
        self.exts = exts
        self.container = list()
        self.browser = webdriver
        self.domain_name = domain_name
        self.page_results = page_results
        self.download_dir = download_dir + "file_downloads/"

    def process_file(self, curr_file):
        """Process the provided file. If the file is a PDF, the PyPDF2 library will be used.
        Otherwise, the extract tool is used, so extract must be installed. This is the one
        piece that requires Linux.
        """
        date = "None"
        modded = "None"
        author = "None"
        created = "None"
        producer = "None"
        last_saved = "None"
        # Process the current file as a PDF
        if ".pdf" in curr_file:
            try:
                pdf_file = PdfFileReader(open(curr_file, "rb"))
                if pdf_file.getIsEncrypted():
                    pdf_file.decrypt('')
                # getDocumentInfo() returns something like:
                # {'/Author': 'Chris Maddalena',
                # '/CreationDate': "D:20131014182824-04'00'",
                # '/Creator': 'Microsoft® Excel® 2013',1
                # '/ModDate': "D:20131015141200-04'00'",
                # '/Producer': 'Microsoft® Excel® 2013'}
                doc_info = pdf_file.getDocumentInfo()
                if not doc_info:
                    return
                if "/CreationDate" in doc_info:
                    data = doc_info["/CreationDate"].strip("D:|'")
                    year = data[0:4]
                    date = data[4:6] + "/" + data[6:8]
                    created_time = data[8:10] + ":" + data[10:12]
                    created_time = time.strftime("%I:%M %p", time.strptime(created_time, "%H:%M"))
                    created = date + "/" + year + " " + created_time

                if "/Author" in doc_info:
                    author = doc_info["/Author"]

                if "/Producer" in doc_info:
                    producer = doc_info["/Producer"].strip("(Windows)")
                    producer = re.sub(r'[^\w]', ' ', producer)
                    while True:
                        if "  " in producer:
                            producer = producer.replace("  ", " ")
                        else:
                            break

                if "/ModDate" in doc_info:
                    data = doc_info["/ModDate"].strip("D:|'")
                    year = data[0:4]
                    date = data[4:6] + "/" + data[6:8]
                    modded_time = data[8:10] + ":" + data[10:12]
                    modded_time = time.strftime("%I:%M %p", time.strptime(modded_time, "%H:%M"))
                    modded = date + "/" + year + " "  + modded_time

                # Strips '/' off filename (if it includes directory name)
                if "/" in curr_file:
                    curr_file = curr_file[curr_file.rfind("/")+1:]
                if "\\" in curr_file:
                    curr_file = curr_file.replace("\\", "")

                # Add the document info to the container
                self.container.append([curr_file, created, author, producer, modded, last_saved])
            except Exception:
                return
        # Not a PDF, so treat the current file as an Office doc
        else:
            curr_file = curr_file.replace(" ", "\ ").replace("(", "\(")\
                .replace(")", "\)")
            try:
                # Unzip the contents of the document to get the contents of core.xml and app.xml files
                unzipped = zipfile.ZipFile(curr_file)
                doc_xml = lxml.etree.fromstring(unzipped.read("docProps/core.xml"))
                app_xml = lxml.etree.fromstring(unzipped.read("docProps/app.xml"))
                # Namespaces for doc.xml
                dc_ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                cp_ns = {"cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"}
                dcterms_ns = {"dcterms": "http://purl.org/dc/terms/"}
                # Namespaces for app.xml
                # app_ns = {"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}
                # vt_ns = {"vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"}
                # tags = doc_xml.xpath('//cp:keywords', namespaces=cp_ns)[0].text
                # description = doc_xml.xpath('//dc:description', namespaces=dc_ns)[0].text
                author = doc_xml.xpath('//dc:creator', namespaces=dc_ns)[0].text
                modded = doc_xml.xpath('//cp:lastModifiedBy', namespaces=cp_ns)[0].text
                created = doc_xml.xpath('//dcterms:created', namespaces=dcterms_ns)[0].text
                last_saved = doc_xml.xpath('//dcterms:modified', namespaces=dcterms_ns)[0].text

                # Convert the created time to a prettier format
                created_date = created.split("T")[0]
                created_time = created.split("T")[1].strip("Z")
                modded_time = time.strftime("%I:%M %p", time.strptime(created_time, "%H:%M:%S"))
                created = created_date + " "  + modded_time

                for child in app_xml:
                    if 'AppVersion' in child.tag:
                        office_version = child.text
                        if "16." in office_version:
                            version = "2016"
                        elif "15." in office_version:
                            version = "2013"
                        elif "14." in office_version:
                            version = "2010"
                        elif "12." in office_version:
                            version = "2007"
                        if ".xls" in curr_file:
                            producer = "Microsoft Excel " + version
                        elif ".doc" in curr_file:
                            producer = "Microsoft Word " + version
                        elif ".ppt" in curr_file:
                            producer = "Microsoft PowerPoint " + version

                if "/" in curr_file:
                    curr_file = curr_file[curr_file.rfind("/")+1:]

                if "\\" in curr_file:
                    curr_file = curr_file.replace("\\", "")

                self.container.append([curr_file, created, author, producer, modded, last_saved])
            except Exception as error:
                click.secho("[!] Failed to extract metadata from {}!".format(curr_file), fg="red")
                click.secho("L.. Details: {}".format(error), fg="red")
                pass

    def grab_meta(self):
        """Extract the metadata from downloaded files."""
        files = []
        total_count = 0
        for extension in self.exts:
            count = 0
            while count < self.page_results:
                headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/11.1.2 Safari/605.1.15'}
                request = requests.get("https://www.google.com/search?q=site:{}+filetype:{}&start={}"\
                    .format(self.domain_name, extension, count), headers=headers)
                contents = request.text
                try:
                    self.browser.get("https://www.google.com/search?q=site:{}+filetype:{}&start={}"\
                        .format(self.domain_name, extension, count))
                    contents = self.browser.page_source
                    if "https://www.google.com/recaptcha/api2/anchor" in contents:
                        click.secho("\n[!] Google returned their recaptcha prompt! File searches \
cannot be performed.", fg="red")
                        exit()
                    new_pattern = "(?P<url>https?://[^:]+\.%s)" % extension
                    new_pattern = re.findall(new_pattern, contents)
                    for hit in new_pattern:
                        if hit not in files:
                            files.append(hit)
                    count += 1
                    total_count += 1
                    # Sleep to try to avoid Google's recaptcha between result pages
                    time.sleep(5)
                except requests.exceptions.Timeout:
                    pass
                except Exception:
                    pass
            # Sleep to try to avoid Google's recaptcha between extension searches
            time.sleep(10)
        if len(files) == 0:
            click.secho("[+] No files were located within Google based on the extension(s) and \
domain you provided.", fg="green")
            exit()
        # Create downloads directory if it doesn't exist
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)
        # Set maximum number of spaces for file names
        spaces = 0
        for item in files:
            item = item[item.rfind("/")+1:]
            short_file = item
            if len(short_file) > spaces:
                spaces = len(short_file) + 3
        # Download each file that has been added to the 'files' list
        for item in files:
            # Throw out any truncated addresses
            if "..." in item:
                del files[files.index(item)]
                continue
            filename = item[item.rfind("/")+1:]
            try:
                response = requests.get(item)
                source = response.content
                with open(self.download_dir + filename, "wb") as file_descriptor:
                    file_descriptor.write(source)
                filename = filename.replace("(", "\(").replace(")", "\)")
                short_file = filename
            except Exception as error:
                click.secho("[!] There was an error downloading a file from this URL: {}"
                             .format(item), fg="red")
                click.secho("L.. Details: {}".format(error), fg="red")
                continue
        for item in files:
            filename = item[item.rfind("/")+1:]
            self.process_file(self.download_dir + filename)
        return self.container

